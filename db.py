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
import os
import pathlib
import sqlite3

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
    """A browser-sign-in connection shared for the life of the process, so a
    script signs in once rather than once per query; close() leaves it open."""

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
    )
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
    return conn


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
            cur.execute(f"CREATE TEMPORARY TABLE _yo_stage LIKE {TABLE}")
            cur.executemany(f"INSERT INTO _yo_stage ({cols}) VALUES ({marks})", rows)
            cur.execute(
                f"MERGE INTO {TABLE} t USING _yo_stage s "
                f"ON t.dedup_hash = s.dedup_hash "
                f"WHEN NOT MATCHED THEN INSERT ({cols}) VALUES "
                f"({', '.join('s.' + c for c in COL_NAMES)})")
            cur.execute("DROP TABLE IF EXISTS _yo_stage")
        else:
            cur.executemany(
                f"INSERT INTO {TABLE} ({cols}) VALUES ({marks}) "
                f"ON CONFLICT(dedup_hash) DO NOTHING", rows)
        conn.commit()
    finally:
        conn.close()
    return len(fresh), len(records) - len(fresh)


def update_rows(items):
    """Apply portal edits, [(dedup_hash, {field: value})], over one connection
    (one Snowflake login for a whole batch, not one per row). Only EDITABLE
    fields are accepted. -> rows updated."""
    items = [(h, {k: v for k, v in ch.items() if k in EDITABLE}) for h, ch in items]
    items = [(h, ch) for h, ch in items if ch]
    if not items:
        return 0
    conn, ph = _connect()
    try:
        cur = conn.cursor()
        n = 0
        for h, changes in items:
            changes = {**changes, "updated_at": _now()}
            sets = ", ".join(f"{k} = {ph}" for k in changes)
            cur.execute(f"UPDATE {TABLE} SET {sets} WHERE dedup_hash = {ph}",
                        tuple(_clean(v) for v in changes.values()) + (h,))
            n += cur.rowcount
        conn.commit()
        return n
    finally:
        conn.close()


def update_row(dedup_hash, changes):
    """Apply portal edits to one row."""
    return update_rows([(dedup_hash, changes)])


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


def count_rows():
    conn, _ = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
        return cur.fetchone()[0]
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


def fetch_decisions():
    """{dedup_hash: {decision, flags, note, decided_by, decided_at}}; empty when
    the table doesn't exist yet (a fresh deployment)."""
    conn, _ = _connect()
    try:
        return _read_decisions(conn.cursor())
    finally:
        conn.close()


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


def set_decisions(items):
    """Record (or replace) decisions, [(dedup_hash, decision, flags, note,
    decided_by)], over one connection."""
    rows = []
    for dedup_hash, decision, flags, note, decided_by in items:
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {DECISIONS}")
        rows.append((dedup_hash, decision, ", ".join(flags) or None, _clean(note),
                     _clean(decided_by) or "portal", _now()))
    if not rows:
        return
    conn, ph = _connect()
    try:
        cur = conn.cursor()
        marks = ", ".join([ph] * len(_REVIEW_NAMES))
        for row in rows:
            if use_snowflake():
                cur.execute(
                    f"MERGE INTO {REVIEW_TABLE} t USING (SELECT {marks}) "
                    f"s ({', '.join(_REVIEW_NAMES)}) ON t.dedup_hash = s.dedup_hash "
                    f"WHEN MATCHED THEN UPDATE SET "
                    + ", ".join(f"{c} = s.{c}" for c in _REVIEW_NAMES[1:])
                    + f" WHEN NOT MATCHED THEN INSERT ({', '.join(_REVIEW_NAMES)}) "
                    f"VALUES ({', '.join('s.' + c for c in _REVIEW_NAMES)})", row)
            else:
                cur.execute(f"INSERT OR REPLACE INTO {REVIEW_TABLE} "
                            f"({', '.join(_REVIEW_NAMES)}) VALUES ({marks})", row)
        conn.commit()
    finally:
        conn.close()


def set_decision(dedup_hash, decision, flags=(), note=None, decided_by=None):
    """Record (or replace) the decision on one report."""
    set_decisions([(dedup_hash, decision, flags, note, decided_by)])


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
