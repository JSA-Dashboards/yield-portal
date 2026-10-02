"""
University variety-trial yields — the second archive in this portal.

Where YIELD_OBSERVATIONS holds one scout's report of a field, these hold what the
state universities published for a whole test site: TRIAL_SITE_YEARS is one row per
location per season, TRIAL_STATE_YEARS is the programme's own average for that season
beside the USDA state yield and the fitted trend.

Both are built in the illinois-corn-trials project by `export_portal_tables.py` and
loaded here by `load_trials.py`. They are read-only in the portal: nothing is keyed in,
and a reload replaces a season wholesale rather than merging, because the upstream
parsers are what change.

Backend follows db.py — Snowflake when USE_SNOWFLAKE is set, else the same local SQLite
file — and the database is pinned there, not read from SNOWFLAKE_DATABASE.
"""
import os

import pandas as pd
import streamlit as st

import db

SITE_TABLE = "TRIAL_SITE_YEARS"
STATE_TABLE = "TRIAL_STATE_YEARS"

SITE_COLUMNS = [
    ("state", "VARCHAR(8)"),             # NE splits into NE-IR / NE-RF by regime
    ("state_name", "VARCHAR(40)"),
    ("crop", "VARCHAR(16)"),
    ("year", "INTEGER"),
    ("site", "VARCHAR(80)"),
    ("grouping", "VARCHAR(16)"),         # what the programme calls its grouping
    ("group_name", "VARCHAR(80)"),
    ("entries", "FLOAT"),                # hybrids or varieties in the test
    ("yield_mean", "FLOAT"),
    ("tests", "FLOAT"),                  # tables pooled into the site-year (MO)
    ("irrigation", "VARCHAR(16)"),       # as the programme designated it
    ("programme", "VARCHAR(80)"),
    ("comparable", "BOOLEAN"),           # a trend over this series would be composition
]
STATE_COLUMNS = [
    ("state", "VARCHAR(8)"),
    ("state_name", "VARCHAR(40)"),
    ("crop", "VARCHAR(16)"),
    ("year", "INTEGER"),
    ("sites", "FLOAT"),
    ("groups", "FLOAT"),
    ("entries", "FLOAT"),
    ("trial_yield", "FLOAT"),
    ("state_yield", "FLOAT"),            # USDA NASS, same state and season
    ("trial_chg", "FLOAT"),
    ("state_chg", "FLOAT"),
    ("spread", "FLOAT"),                 # trial minus state
    ("trend", "FLOAT"),                  # fitted value for the season
    ("vs_trend", "FLOAT"),
    ("pct_of_trend", "FLOAT"),
    ("trend_rate", "FLOAT"),             # bu/acre/year over the whole series
    ("corr", "FLOAT"),                   # trial vs state year-over-year change
    ("n_paired", "FLOAT"),
    ("programme", "VARCHAR(80)"),
]
TABLES = {SITE_TABLE: SITE_COLUMNS, STATE_TABLE: STATE_COLUMNS}

CROP_LABEL = {"corn": "Corn", "soybeans": "Soybeans"}


class NotLoaded(Exception):
    """The trial tables aren't in this database yet.

    They are loaded by hand from a sibling project, so a fresh clone or a second
    environment has neither, and Snowflake reports a missing table as an error rather
    than an empty result.
    """


def create_tables(cur=None):
    """Create both tables if they are missing. Local SQLite does it on page load;
    on Snowflake load_trials.py does it over its own connection."""
    def ddl(table):
        cols = ",\n    ".join("%s %s" % (c, t) for c, t in TABLES[table])
        return "CREATE TABLE IF NOT EXISTS %s (\n    %s\n)" % (table, cols)

    if cur is not None:
        for table in TABLES:
            cur.execute(ddl(table))
        return
    conn, _ = db._connect()
    try:
        c = conn.cursor()
        for table in TABLES:
            c.execute(ddl(table))
        conn.commit()
    finally:
        conn.close()


def replace(table, rows):
    """Replace a table's contents with these rows, in one transaction.

    A reload follows an upstream parser change, so the rows are not merged on a key:
    the point is that a site-year's yield can legitimately change when the extractor
    improves, and a merge would keep the old figure.
    """
    cols = [c for c, _ in TABLES[table]]
    conn, ph = db._connect()
    try:
        cur = conn.cursor()
        # dropped rather than emptied, so a column added upstream lands without an
        # ALTER: these tables are replace-only, with nothing keyed in to lose
        cur.execute("DROP TABLE IF EXISTS %s" % table)
        create_tables(cur)
        if rows:
            marks = ", ".join([ph] * len(cols))
            cur.executemany(
                "INSERT INTO %s (%s) VALUES (%s)" % (table, ", ".join(cols), marks),
                [tuple(db._clean(r.get(c)) for c in cols) for r in rows])
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def _fetch(cur, table):
    cols = [c for c, _ in TABLES[table]]
    # "group" is reserved on Snowflake and "year" is awkward on some engines, so
    # every column is quoted rather than relying on which names happen to be safe
    if db.use_snowflake():
        quoted = ", ".join('"%s"' % c.upper() for c in cols)
    else:
        quoted = ", ".join(cols)
    cur.execute("SELECT %s FROM %s" % (quoted, table))
    return pd.DataFrame(cur.fetchall(), columns=cols)


@st.cache_data(ttl=600, show_spinner="Loading variety trials…")
def load_all():
    """Both tables over one connection — opening a Snowflake session costs seconds and
    the page needs them together. -> (site_years, state_years)."""
    conn, _ = db._connect()
    try:
        cur = conn.cursor()
        try:
            sites = _fetch(cur, SITE_TABLE)
            states = _fetch(cur, STATE_TABLE)
        except Exception as exc:
            text = str(exc).lower()
            if "does not exist" in text or "no such table" in text:
                raise NotLoaded(str(exc))
            raise
    finally:
        conn.close()
    return _typed_sites(sites), _typed_states(states)


def _typed_sites(d):
    """One row per location per season. A few thousand rows, so pages load the whole
    table once and filter it in memory."""
    d["year"] = pd.to_numeric(d["year"], errors="coerce").astype("Int64")
    for c in ("entries", "yield_mean", "tests"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["comparable"] = d.comparable.map(
        lambda v: str(v).strip().lower() in ("1", "true", "yes"))
    d["crop_label"] = d.crop.map(CROP_LABEL).fillna(d.crop)
    return d


def _typed_states(d):
    """One row per state-year: the programme's average, the USDA state yield, and the
    fitted trend."""
    d["year"] = pd.to_numeric(d["year"], errors="coerce").astype("Int64")
    for c, _t in STATE_COLUMNS:
        if c not in ("state", "state_name", "crop", "year", "programme"):
            d[c] = pd.to_numeric(d[c], errors="coerce")
    d["crop_label"] = d.crop.map(CROP_LABEL).fillna(d.crop)
    return d


def invalidate():
    load_all.clear()


def source_csvs():
    """Where load_trials.py looks for the exported tables: the sibling project, or
    TRIALS_DATA_DIR if it lives somewhere else on this machine."""
    override = (os.environ.get("TRIALS_DATA_DIR") or "").strip()
    if override:
        return override
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "illinois-corn-trials", "data")
