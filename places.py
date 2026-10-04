"""
Which NASS county a report is from: a key derived from its location text, which
itself is never changed.

  exact      the text names a county ("Vermilion Co", "Northern Vermilion County")
             that NASS has in that state -> used straight away
  several    "Moultrie/Coles Co", "Pipestone & Rock Co" -> all of them, averaged
  suggested  a near-spelling of a county ("Vermillion Co" -> Vermilion), or a bare
             place that shares a county's name ("Peoria") -> waits for a person
  confirmed  a suggestion a person accepted (COUNTY_MATCHES)
  none       a town, region or anything else -> the state's numbers are used

Towns are not looked up: telling Mankato from Blue Earth County takes a
gazetteer, and a wrong county is worse than the state.
"""
import difflib
import re

import streamlit as st

import db

TABLE = "COUNTY_MATCHES"
COLUMNS = [("state", "VARCHAR(2) NOT NULL"), ("place_key", "VARCHAR(120) NOT NULL"),
           ("decision", "VARCHAR(12) NOT NULL"), ("counties", "VARCHAR(400)"),
           ("fips", "VARCHAR(200)"), ("decided_by", "VARCHAR(60)"), ("decided_at", "TIMESTAMP")]
_NAMES = [c for c, _ in COLUMNS]
_DIRECTION = (r"(?:far\s+)?(?:north|south|east|west|central|northern|southern|eastern|western|"
              r"northeast(?:ern)?|northwest(?:ern)?|southeast(?:ern)?|southwest(?:ern)?|"
              r"ne|nw|se|sw|nc|sc|ec|wc)")
_COUNTY_WORD = re.compile(r"\b(?:co\.?|county|parish)\b", re.I)
_SPLIT = re.compile(r"\s*(?:/|&|\band\b|,)\s*", re.I)


def pretty(name) -> str:
    """NASS's 'MCLEAN' / 'ST CLAIR' / 'DE KALB' -> 'McLean' / 'St. Clair' / 'De Kalb'."""
    s = str(name or "").title()
    s = re.sub(r"\bMc(\w)", lambda m: "Mc" + m.group(1).upper(), s)
    s = re.sub(r"\bSt\b\.?", "St.", s)
    return re.sub(r"\bSte\b\.?", "Ste.", s)


def norm(name) -> str:
    """'St. Clair' / 'Saint Clair' / 'ST CLAIR' -> 'stclair'; 'Mc Lean' -> 'mclean'."""
    s = str(name or "").lower().replace("saint ", "st ").replace("sainte ", "ste ")
    return re.sub(r"[^a-z]", "", s)


def place_key(location) -> str:
    """The location reduced to what identifies the place: direction words and the
    county word dropped ("Northern Vermilion County" -> "vermilion")."""
    s = _COUNTY_WORD.sub(" ", str(location or ""))
    s = re.sub(r"[.;:]+", " ", s)
    s = re.sub(rf"^\s*(?:{_DIRECTION}\s+)+", "", s, flags=re.I)
    return " ".join(s.split()).lower()


def _parts(location):
    return [norm(re.sub(rf"^\s*(?:{_DIRECTION}\s+)+", "", p, flags=re.I))
            for p in _SPLIT.split(place_key(location)) if p.strip()]


def ensure_table():
    cols = ",\n    ".join(f"{c} {t}" for c, t in COLUMNS)
    conn, _ = db._connect()
    try:
        conn.cursor().execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (\n    {cols},\n"
                              f"    PRIMARY KEY (state, place_key)\n)")
        conn.commit()
    finally:
        conn.close()


def fetch_decisions() -> dict:
    """{(state, place_key): {decision, counties, fips}}; {} before the table exists."""
    conn, _ = db._connect()
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT {', '.join(_NAMES)} FROM {TABLE}")
        except Exception as exc:
            if "does not exist" in str(exc).lower() or "no such table" in str(exc).lower():
                return {}
            raise
        names = [d[0].lower() for d in cur.description]
        return {(r[0], r[1]): dict(zip(names, r)) for r in cur.fetchall()}
    finally:
        conn.close()


@st.cache_data(ttl=600, show_spinner=False)
def cached_decisions() -> dict:
    """fetch_decisions() for the pages; decide() clears it."""
    return fetch_decisions()


def decide(state, key, decision, counties=(), fips=(), decided_by=None):
    """Record 'confirmed' (with the counties) or 'rejected' for one place."""
    ensure_table()
    row = (state, key, decision, ", ".join(counties) or None, ", ".join(fips) or None,
           decided_by or "portal", db._now())
    conn, ph = db._connect()
    try:
        cur = conn.cursor()
        marks = ", ".join([ph] * len(_NAMES))
        if db.use_snowflake():
            cur.execute(
                f"MERGE INTO {TABLE} t USING (SELECT {marks}) s ({', '.join(_NAMES)}) "
                f"ON t.state = s.state AND t.place_key = s.place_key "
                f"WHEN MATCHED THEN UPDATE SET " + ", ".join(f"{c} = s.{c}" for c in _NAMES[2:])
                + f" WHEN NOT MATCHED THEN INSERT ({', '.join(_NAMES)}) "
                f"VALUES ({', '.join('s.' + c for c in _NAMES)})", row)
        else:
            cur.execute(f"INSERT OR REPLACE INTO {TABLE} ({', '.join(_NAMES)}) VALUES ({marks})", row)
        conn.commit()
    finally:
        conn.close()
    cached_decisions.clear()


def match(location, state, index: dict, decisions: dict):
    """-> (method, [county names], [fips], suggestion text or None)."""
    counties = index.get(state, {})
    by_norm = {norm(n): (n, f) for n, f in counties.items()}
    key = place_key(location)
    if not key:
        return "none", [], [], None
    d = decisions.get((state, key))
    if d and d["decision"] == "confirmed":
        return "confirmed", (d["counties"] or "").split(", "), (d["fips"] or "").split(", "), None
    rejected = bool(d and d["decision"] == "rejected")
    parts = _parts(location)
    found = [by_norm[p] for p in parts if p in by_norm]
    named = bool(_COUNTY_WORD.search(str(location or "")))
    if found and len(found) == len(parts):
        names, fips = [n for n, _ in found], [f for _, f in found]
        if named:
            return ("several" if len(found) > 1 else "exact"), names, fips, None
        if not rejected:                 # a bare "Peoria": probably, but ask
            return "suggested", names, fips, f"{' / '.join(pretty(n) for n in names)} County?"
        return "none", [], [], None
    if named and len(parts) == 1 and not rejected:
        close = difflib.get_close_matches(parts[0], list(by_norm), n=1, cutoff=0.85)
        if close:
            n, f = by_norm[close[0]]
            return "suggested", [n], [f], f"{pretty(n)} County?"
    return "none", [], [], None


def match_all(df, index: dict, decisions: dict):
    """Columns county_method, county_names, county_fips, county_suggestion."""
    res = [match(loc, st_, index, decisions) for loc, st_ in zip(df["location"], df["state"])]
    out = df.copy()
    out["county_method"] = [r[0] for r in res]
    out["county_names"] = [r[1] for r in res]
    out["county_fips"] = [r[2] for r in res]
    out["county_suggestion"] = [r[3] for r in res]
    out["place_key"] = df["location"].map(place_key)
    return out
