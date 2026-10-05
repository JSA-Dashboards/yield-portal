"""
Storage for the Yield Portal.

One table, YIELD_OBSERVATIONS: one row per field report from the Ag
Trader Talk yield list — from the annual PDFs, from individual emails, or keyed in by hand.

Backend: Snowflake when USE_SNOWFLAKE is truthy (prod: database YIELD_REPORTS on
the Analytics account), otherwise a local SQLite file (dev). Same SQL both ways
except the bulk upsert (MERGE on Snowflake, ON CONFLICT on SQLite).

dedup_hash is a row's identity. It is computed once from the source text at
ingest and never recomputed, so editing a row in the portal and then
re-importing the same PDF cannot create a duplicate. Imports only ever insert
rows whose hash is new; they never overwrite an existing (possibly edited) row.
"""
import datetime as _dt
import functools
import os
import pathlib
import sqlite3
import threading
import uuid

HERE = pathlib.Path(__file__).resolve().parent
LOCAL_SQLITE = HERE / "yield_portal.db"
TABLE = "YIELD_OBSERVATIONS"

# The database/schema are pinned here rather than read from SNOWFLAKE_DATABASE /
# SNOWFLAKE_SCHEMA: a multi-app host (e.g. jsa-admin-portal) sets those globally
# for a different app, and inheriting them would point this module at the wrong
# database and silently show nothing.
SF_DATABASE = os.environ.get("YIELD_DATABASE") or "YIELD_REPORTS"
SF_SCHEMA = os.environ.get("YIELD_SCHEMA") or "PUBLIC"

# (name, type). The same DDL works on both engines: SQLite maps these type
# names onto its own affinities.
COLUMNS = [
    ("dedup_hash", "VARCHAR(32) NOT NULL PRIMARY KEY"),
    ("crop_year", "INTEGER"),
    ("date_reported", "DATE"),
    ("crop", "VARCHAR(16)"),
    ("state", "VARCHAR(2)"),
    ("location", "VARCHAR(200)"),
    ("yield_bpa", "FLOAT"),
    ("yield_min", "FLOAT"),
    ("yield_max", "FLOAT"),
    ("ly_yield", "FLOAT"),
    ("expected_yield", "FLOAT"),
    ("aph", "FLOAT"),
    ("maturity", "VARCHAR(16)"),          # corn RM days "108-112" / soy MG "2.6"
    ("irrigation", "VARCHAR(16)"),        # Irrigated | Non-irrigated | Mixed | NULL = not stated
    ("disease", "VARCHAR(300)"),          # comma-joined tags, e.g. "Tar spot, Drought/dry"
    ("is_silage", "BOOLEAN"),
    ("is_record", "BOOLEAN"),
    ("raw_text", "VARCHAR(4000)"),
    ("report_source", "VARCHAR(16)"),     # pdf | email | manual
    ("source_file", "VARCHAR(300)"),
    ("email_subject", "VARCHAR(300)"),
    ("email_id", "VARCHAR(300)"),         # Internet Message-ID of the matched email
    ("notes", "VARCHAR(2000)"),
    ("created_at", "TIMESTAMP"),
    ("updated_at", "TIMESTAMP"),
]
COL_NAMES = [c for c, _ in COLUMNS]
# Fields a user may change from the portal's edit form.
EDITABLE = ["crop_year", "date_reported", "crop", "state", "location",
            "yield_bpa", "yield_min", "yield_max", "ly_yield", "expected_yield",
            "aph", "maturity", "irrigation", "disease", "is_silage",
            "is_record", "raw_text", "notes"]


# --- backend ----------------------------------------------------------------
def use_snowflake():
    return os.environ.get("USE_SNOWFLAKE", "").strip().lower() in (
        "1", "true", "yes", "on")


def backend_name():
    if use_snowflake():
        return f"Snowflake ({SF_DATABASE}.{SF_SCHEMA})"
    return f"SQLite ({LOCAL_SQLITE.name})"


_BASE64 = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")


def _pem_bytes(text):
    """A PEM key as pasted into Streamlit secrets -> bytes for cryptography.
    Literal \\n, CRLF and indented lines are forgiven. Anything else that isn't
    key text (an example's '…' placeholder, a smart quote) is named by
    character and line, never quoting the key, instead of cryptography's
    'Invalid symbol 226, offset 0'."""
    lines = [ln.strip() for ln in text.replace("\\n", "\n").splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines or not lines[0].startswith("-----BEGIN "):
        raise ValueError("SNOWFLAKE_PRIVATE_KEY must start with its "
                         "-----BEGIN PRIVATE KEY----- line.")
    for n, ln in enumerate(lines[1:-1], 1):
        bad = next((c for c in ln if c not in _BASE64), None)
        if bad is not None:
            raise ValueError(
                f"SNOWFLAKE_PRIVATE_KEY isn't the real key: line {n} after BEGIN has "
                f"{bad!r}, which never appears in a key (an example placeholder?). "
                "Paste the whole .p8 key file between the triple quotes.")
    return ("\n".join(lines) + "\n").encode()


def _load_private_key():
    """RSA key for Snowflake key-pair auth, as DER bytes; None if not configured
    (then password auth). Source: SNOWFLAKE_PRIVATE_KEY_PATH (.p8 file) or
    SNOWFLAKE_PRIVATE_KEY (PEM text, as pasted into Streamlit secrets)."""
    path = (os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH") or "").strip()
    pem = os.environ.get("SNOWFLAKE_PRIVATE_KEY") or ""
    if not path and not pem.strip():
        return None
    from cryptography.hazmat.primitives import serialization
    data = open(path, "rb").read() if path else _pem_bytes(pem)
    pwd = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PWD") or None
    key = serialization.load_pem_private_key(
        data, password=pwd.encode() if pwd else None)
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption())


class _KeptOpen:
    """A Snowflake connection shared for the life of the process, so the app logs
    in once instead of once per query (a login costs 2-3 s; the query itself well
    under 1 s). close() leaves it open. Every caller takes its own cursor, which
    the connector allows across threads."""

    def __init__(self, conn):
        self.raw = conn

    def cursor(self):
        return self.raw.cursor()

    def commit(self):
        return self.raw.commit()

    def rollback(self):
        return self.raw.rollback()

    def close(self):
        pass


_SHARED = {}
_SHARED_LOCK = threading.Lock()


def _dead_session(exc) -> bool:
    """A Snowflake error that means the shared session is gone, not the query."""
    msg = str(exc).lower()
    return any(s in msg for s in ("session no longer exists", "token has expired",
                                  "connection is closed", "session expired", "390111",
                                  "390112", "390114", "250002"))


def _retry_once(fn):
    """Run fn; if the shared Snowflake session died under it, log in again and
    run it once more. (A dead session ran nothing, so a write is safe to redo.)"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if not (use_snowflake() and _dead_session(exc)):
                raise
            with _SHARED_LOCK:
                _SHARED.clear()
            return fn(*args, **kwargs)
    return wrapper


def sf_connect(database=SF_DATABASE, schema=SF_SCHEMA):
    """Snowflake connection. Pass database=None/schema=None to connect before
    YIELD_REPORTS exists (the setup script does).

    SNOWFLAKE_CONNECTION_NAME picks a profile from ~/.snowflake/connections.toml
    — for local runs against the Analytics account, whose profile signs in
    through the browser and caches the token. Otherwise the SNOWFLAKE_* env
    (key-pair or password), which is what the deployed app uses."""
    # import_module rather than `import snowflake.connector as sc`: it returns the
    # submodule from sys.modules instead of reading it off the parent, so it holds up
    # when something has just dropped `snowflake` from sys.modules. That used to happen
    # here every time a file changed — the project's own snowflake/ folder made the
    # parent a watched local module — and cost two sessions an afternoon to
    # "module 'snowflake' has no attribute 'connector'". The folder is snowflake_admin/
    # now, so the collision is gone; this stays as the cheaper of the two defences.
    import importlib
    sc = importlib.import_module("snowflake.connector")
    named = (os.environ.get("SNOWFLAKE_CONNECTION_NAME") or "").strip()
    if named:
        key = (named, database, schema)
        held = _SHARED.get(key)
        if held is not None and not held.raw.is_closed():
            return held
        # Token caching off: with `keyring` installed the connector stores the
        # OAuth token in Windows Credential Manager, which rejects a token that
        # size ("CredWrite: The stub received bad data") and the connect fails.
        kw = {"connection_name": named, "login_timeout": 180,
              "client_store_temporary_credential": False}
        if database:
            kw["database"] = database
        if schema:
            kw["schema"] = schema
        # The Analytics profile has no default warehouse (COMPUTE_WH is no one's
        # default), so honor an explicit SNOWFLAKE_WAREHOUSE on the profile path too
        # — otherwise every query fails with "No active warehouse selected".
        wh = os.environ.get("SNOWFLAKE_WAREHOUSE")
        if wh:
            kw["warehouse"] = wh
        conn = sc.connect(**kw)
        try:
            conn._paramstyle = "pyformat"
        except Exception:
            pass
        _SHARED[key] = _KeptOpen(conn)
        return _SHARED[key]
    kw = dict(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        role=os.environ.get("SNOWFLAKE_ROLE") or None,
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE") or None,
        database=database or None,
        schema=schema or None,
        login_timeout=30,
        network_timeout=60,
        client_session_keep_alive=True,    # the shared session outlives a quiet hour
    )
    key = ("env",) + tuple(kw[k] for k in ("account", "user", "role", "warehouse",
                                           "database", "schema"))
    with _SHARED_LOCK:
        held = _SHARED.get(key)
        if held is not None and not held.raw.is_closed():
            return held
        pkey = _load_private_key()
        if pkey is not None:
            kw["private_key"] = pkey
        else:
            kw["password"] = os.environ.get("SNOWFLAKE_PASSWORD") or None
        conn = sc.connect(**{k: v for k, v in kw.items() if v is not None})
        try:
            conn._paramstyle = "pyformat"   # %s binding, same as the SQLite path's ?
        except Exception:
            pass
        _SHARED[key] = _KeptOpen(conn)
        return _SHARED[key]


def _connect():
    """-> (connection, placeholder)."""
    if use_snowflake():
        return sf_connect(), "%s"
    return sqlite3.connect(LOCAL_SQLITE), "?"


def _ddl():
    cols = ",\n    ".join(f"{c} {t}" for c, t in COLUMNS)
    return f"CREATE TABLE IF NOT EXISTS {TABLE} (\n    {cols}\n)"


def init_db():
    """Create the tables locally. On Snowflake they are created once by
    snowflake_admin/setup.py, so app start does no DDL there."""
    if use_snowflake():
        return
    conn, _ = _connect()
    try:
        conn.execute(_ddl())
        conn.commit()
    finally:
        conn.close()
    ensure_review_table()


# --- helpers ----------------------------------------------------------------
def _now():
    return _dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _clean(v):
    """Normalise a value for binding: NaN/'' -> None, numpy scalars -> python."""
    if v is None:
        return None
    try:
        import math
        if isinstance(v, float) and math.isnan(v):
            return None
    except Exception:
        pass
    if hasattr(v, "item"):            # numpy / pandas scalar
        try:
            v = v.item()
        except Exception:
            pass
    if isinstance(v, str) and not v.strip():
        return None
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    return v


def _row_tuple(rec):
    return tuple(_clean(rec.get(c)) for c in COL_NAMES)


# --- writes -----------------------------------------------------------------
@_retry_once
def insert_new(records):
    """Insert records whose dedup_hash isn't stored yet; skip the rest.
    Never overwrites an existing row. -> (inserted, skipped)."""
    if not records:
        return 0, 0
    now = _now()
    for r in records:
        r.setdefault("created_at", now)
        r.setdefault("updated_at", now)
    hashes = [r["dedup_hash"] for r in records]
    existing = existing_hashes(hashes)
    fresh, seen = [], set()
    for r in records:
        h = r["dedup_hash"]
        if h in existing or h in seen:      # also collapses dupes within the batch
            continue
        seen.add(h)
        fresh.append(r)
    if not fresh:
        return 0, len(records)

    conn, ph = _connect()
    try:
        cur = conn.cursor()
        cols = ", ".join(COL_NAMES)
        marks = ", ".join([ph] * len(COL_NAMES))
        rows = [_row_tuple(r) for r in fresh]
        if use_snowflake():
            # Stage into a temp table, then MERGE: insert only unseen hashes even
            # if another session added some since existing_hashes() ran.
            # a name of its own: the session is shared, and two imports at once
            # must not stage into the same temp table
            stage = f"_yo_stage_{uuid.uuid4().hex[:10]}"
            cur.execute(f"CREATE TEMPORARY TABLE {stage} LIKE {TABLE}")
            cur.executemany(f"INSERT INTO {stage} ({cols}) VALUES ({marks})", rows)
            cur.execute(
                f"MERGE INTO {TABLE} t USING {stage} s "
                f"ON t.dedup_hash = s.dedup_hash "
                f"WHEN NOT MATCHED THEN INSERT ({cols}) VALUES "
                f"({', '.join('s.' + c for c in COL_NAMES)})")
            cur.execute(f"DROP TABLE IF EXISTS {stage}")
        else:
            cur.executemany(
                f"INSERT INTO {TABLE} ({cols}) VALUES ({marks}) "
                f"ON CONFLICT(dedup_hash) DO NOTHING", rows)
        conn.commit()
    finally:
        conn.close()
    return len(fresh), len(records) - len(fresh)


@_retry_once
def update_row(dedup_hash, changes):
    """Apply portal edits to one row. Only EDITABLE fields are accepted."""
    changes = {k: v for k, v in changes.items() if k in EDITABLE}
    if not changes:
        return 0
    changes["updated_at"] = _now()
    conn, ph = _connect()
    try:
        cur = conn.cursor()
        sets = ", ".join(f"{k} = {ph}" for k in changes)
        cur.execute(f"UPDATE {TABLE} SET {sets} WHERE dedup_hash = {ph}",
                    tuple(_clean(v) for v in changes.values()) + (dedup_hash,))
        n = cur.rowcount
        conn.commit()
        return n
    finally:
        conn.close()


@_retry_once
def set_reported(dedup_hash, date_reported, email_subject=None, email_id=None):
    """Stamp the date an observation was reported (from its matching email)."""
    conn, ph = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE {TABLE} SET date_reported = {ph}, email_subject = {ph}, "
            f"email_id = {ph}, updated_at = {ph} WHERE dedup_hash = {ph}",
            (_clean(date_reported), email_subject, email_id, _now(), dedup_hash))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


@_retry_once
def delete_row(dedup_hash):
    conn, ph = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM {TABLE} WHERE dedup_hash = {ph}", (dedup_hash,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# --- reads ------------------------------------------------------------------
@_retry_once
def existing_hashes(hashes):
    """Subset of `hashes` already stored."""
    if not hashes:
        return set()
    found = set()
    conn, ph = _connect()
    try:
        cur = conn.cursor()
        hashes = list(dict.fromkeys(hashes))
        for i in range(0, len(hashes), 500):
            chunk = hashes[i:i + 500]
            cur.execute(
                f"SELECT dedup_hash FROM {TABLE} WHERE dedup_hash IN "
                f"({', '.join([ph] * len(chunk))})", tuple(chunk))
            found.update(r[0] for r in cur.fetchall())
        return found
    finally:
        conn.close()


@_retry_once
def fetch_all():
    """Every observation as a list of dicts (lower-case keys on both engines)."""
    conn, _ = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT {', '.join(COL_NAMES)} FROM {TABLE}")
        names = [d[0].lower() for d in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]
    finally:
        conn.close()


@_retry_once
def count_rows():
    conn, _ = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
        return cur.fetchone()[0]
    finally:
        conn.close()


# --- automatic email pickup ------------------------------------------------------
# One row per run of `match_emails.py --auto` (a scheduled task on Kolten's PC), so
# the portal can say when the emails were last checked and what came in.
SYNC_TABLE = "EMAIL_SYNC_RUNS"
_SYNC_COLUMNS = [("run_at", "TIMESTAMP"), ("host", "VARCHAR(60)"), ("emails", "INTEGER"),
                 ("dated", "INTEGER"), ("added", "INTEGER"), ("needs_review", "INTEGER"),
                 ("error", "VARCHAR(1000)")]


@_retry_once
def record_sync_run(host, emails=0, dated=0, added=0, needs_review=0, error=None):
    """Log one email-sync run (run_at in UTC)."""
    cols = ", ".join(c for c, _ in _SYNC_COLUMNS)
    conn, ph = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f"CREATE TABLE IF NOT EXISTS {SYNC_TABLE} ("
                    + ", ".join(f"{c} {t}" for c, t in _SYNC_COLUMNS) + ")")
        now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None, microsecond=0)
        cur.execute(f"INSERT INTO {SYNC_TABLE} ({cols}) VALUES ({', '.join([ph] * len(_SYNC_COLUMNS))})",
                    (now.isoformat(sep=" "), host, emails, dated, added, needs_review,
                     (error or "")[:1000] or None))
        conn.commit()
    finally:
        conn.close()


@_retry_once
def last_sync_run():
    """The latest email-sync run as a dict (run_at is UTC), or None."""
    conn, _ = _connect()
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT {', '.join(c for c, _ in _SYNC_COLUMNS)} FROM {SYNC_TABLE} "
                        f"ORDER BY run_at DESC LIMIT 1")
        except Exception as exc:
            if "does not exist" in str(exc).lower() or "no such table" in str(exc).lower():
                return None
            raise
        row = cur.fetchone()
        return dict(zip([c for c, _ in _SYNC_COLUMNS], row)) if row else None
    finally:
        conn.close()


# --- the weekly email ----------------------------------------------------------------
# One row per scheduled send of weekly_email.py, so whichever machine sends it
# (the Droplet, or the PC before it) sees that this week's is out, and a second
# scheduler left switched on can't send it again.
WEEKLY_TABLE = "WEEKLY_EMAILS"
_WEEKLY_COLUMNS = [("week", "VARCHAR(10)"), ("sent_at", "TIMESTAMP"), ("host", "VARCHAR(60)"),
                   ("recipient", "VARCHAR(300)"), ("via", "VARCHAR(10)")]


@_retry_once
def record_weekly_email(week, host, recipient, via):
    """Log one weekly-email send (sent_at in UTC)."""
    cols = ", ".join(c for c, _ in _WEEKLY_COLUMNS)
    conn, ph = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f"CREATE TABLE IF NOT EXISTS {WEEKLY_TABLE} ("
                    + ", ".join(f"{c} {t}" for c, t in _WEEKLY_COLUMNS) + ")")
        now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None, microsecond=0)
        cur.execute(f"INSERT INTO {WEEKLY_TABLE} ({cols}) "
                    f"VALUES ({', '.join([ph] * len(_WEEKLY_COLUMNS))})",
                    (week, now.isoformat(sep=" "), host, recipient, via))
        conn.commit()
    finally:
        conn.close()


@_retry_once
def weekly_email_sent(week):
    """The send recorded for an ISO week ("2026-W41") as a dict, or None."""
    conn, ph = _connect()
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT {', '.join(c for c, _ in _WEEKLY_COLUMNS)} FROM {WEEKLY_TABLE} "
                        f"WHERE week = {ph} ORDER BY sent_at DESC LIMIT 1", (week,))
        except Exception as exc:
            if "does not exist" in str(exc).lower() or "no such table" in str(exc).lower():
                return None
            raise
        row = cur.fetchone()
        return dict(zip([c for c, _ in _WEEKLY_COLUMNS], row)) if row else None
    finally:
        conn.close()


# --- review decisions ---------------------------------------------------------
# What a person decided about a flagged report (checks.py raises the flags on
# every load). Its own table, so YIELD_OBSERVATIONS never changes shape and the
# portal's service login (which may create tables but not alter this one) can
# make it. A report with no row here has never been decided on.
#   approved   counts in averages despite the flags listed in `flags`
#   excluded   stays in the archive, never in averages
#   superseded replaced by other rows (e.g. a merged PDF line split in two);
#              hidden everywhere but the audit trail
REVIEW_TABLE = "REVIEW_DECISIONS"
DECISIONS = ("approved", "excluded", "superseded")
REVIEW_COLUMNS = [
    ("dedup_hash", "VARCHAR(32) NOT NULL PRIMARY KEY"),
    ("decision", "VARCHAR(16) NOT NULL"),
    ("flags", "VARCHAR(200)"),
    ("note", "VARCHAR(1000)"),
    ("decided_by", "VARCHAR(60)"),
    ("decided_at", "TIMESTAMP"),
]
_REVIEW_NAMES = [c for c, _ in REVIEW_COLUMNS]


@_retry_once
def ensure_review_table():
    """Create REVIEW_DECISIONS if it isn't there (either backend; idempotent)."""
    cols = ",\n    ".join(f"{c} {t}" for c, t in REVIEW_COLUMNS)
    conn, _ = _connect()
    try:
        conn.cursor().execute(f"CREATE TABLE IF NOT EXISTS {REVIEW_TABLE} (\n    {cols}\n)")
        conn.commit()
    finally:
        conn.close()


def _read_decisions(cur):
    try:
        cur.execute(f"SELECT {', '.join(_REVIEW_NAMES)} FROM {REVIEW_TABLE}")
    except Exception as exc:
        if "does not exist" in str(exc).lower() or "no such table" in str(exc).lower():
            return {}
        raise
    names = [d[0].lower() for d in cur.description]
    return {row[0]: dict(zip(names, row)) for row in cur.fetchall()}


@_retry_once
def fetch_decisions():
    """{dedup_hash: {decision, flags, note, decided_by, decided_at}}; empty when
    the table doesn't exist yet (a fresh deployment)."""
    conn, _ = _connect()
    try:
        return _read_decisions(conn.cursor())
    finally:
        conn.close()


@_retry_once
def fetch_all_and_decisions():
    """fetch_all() and fetch_decisions() over one connection (one Snowflake
    login, not two, on every page load)."""
    conn, _ = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT {', '.join(COL_NAMES)} FROM {TABLE}")
        names = [d[0].lower() for d in cur.description]
        rows = [dict(zip(names, row)) for row in cur.fetchall()]
        return rows, _read_decisions(cur)
    finally:
        conn.close()


@_retry_once
def set_decision(dedup_hash, decision, flags=(), note=None, decided_by=None):
    """Record (or replace) the decision on one report."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    row = (dedup_hash, decision, ", ".join(flags) or None, _clean(note),
           _clean(decided_by) or "portal", _now())
    conn, ph = _connect()
    try:
        cur = conn.cursor()
        marks = ", ".join([ph] * len(_REVIEW_NAMES))
        if use_snowflake():
            cur.execute(
                f"MERGE INTO {REVIEW_TABLE} t USING (SELECT {marks}) "
                f"s ({', '.join(_REVIEW_NAMES)}) ON t.dedup_hash = s.dedup_hash "
                f"WHEN MATCHED THEN UPDATE SET "
                + ", ".join(f"{c} = s.{c}" for c in _REVIEW_NAMES[1:])
                + f" WHEN NOT MATCHED THEN INSERT ({', '.join(_REVIEW_NAMES)}) "
                f"VALUES ({', '.join('s.' + c for c in _REVIEW_NAMES)})", row)
        else:
            cur.execute(f"INSERT OR REPLACE INTO {REVIEW_TABLE} ({', '.join(_REVIEW_NAMES)}) "
                        f"VALUES ({marks})", row)
        conn.commit()
    finally:
        conn.close()


@_retry_once
def clear_decision(dedup_hash):
    """Undo a decision: the report goes back to whatever the checks say."""
    conn, ph = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM {REVIEW_TABLE} WHERE dedup_hash = {ph}", (dedup_hash,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
