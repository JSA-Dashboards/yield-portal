"""
County geography for the missing-county baselines (baselines.py): each county's
centre point and the counties that share its border, from the Census Bureau's
2023 county gazetteer and county adjacency file (public domain).

Loaded once into COUNTY_GEO by load_geo.py; the portal reads it from there,
like the trial tables. Where it isn't loaded (local SQLite, a fresh
environment) `load` returns an empty frame and the baselines simply skip the
neighbor step.
"""
import pandas as pd
import streamlit as st

import db

TABLE = "COUNTY_GEO"
COLUMNS = [("fips", "VARCHAR(5)"), ("state", "VARCHAR(2)"), ("name", "VARCHAR(80)"),
           ("lat", "FLOAT"), ("lon", "FLOAT"),
           ("neighbors", "VARCHAR(300)")]          # FIPS sharing a border, space-separated
GAZETTEER_URL = ("https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
                 "2023_Gazetteer/2023_Gaz_counties_national.zip")
ADJACENCY_URL = ("https://www2.census.gov/geo/docs/reference/county_adjacency/"
                 "county_adjacency2023.txt")


def from_census(gazetteer_txt, adjacency_txt) -> pd.DataFrame:
    """The two Census files -> one row per county: fips, state, name, lat, lon,
    neighbors (a county isn't its own neighbor, though the file lists it so)."""
    g = pd.read_csv(gazetteer_txt, sep="\t", dtype={"GEOID": str}, encoding="latin-1")
    g.columns = [c.strip() for c in g.columns]       # the last header carries padding
    adj = pd.read_csv(adjacency_txt, sep="|", dtype=str, encoding="latin-1")
    adj = adj[adj["County GEOID"] != adj["Neighbor GEOID"]]
    nb = adj.groupby("County GEOID")["Neighbor GEOID"].apply(lambda s: " ".join(sorted(set(s))))
    out = pd.DataFrame({"fips": g["GEOID"].str.zfill(5), "state": g["USPS"], "name": g["NAME"],
                        "lat": pd.to_numeric(g["INTPTLAT"]), "lon": pd.to_numeric(g["INTPTLONG"])})
    out["neighbors"] = out["fips"].map(nb).fillna("")
    return out


def replace(frame: pd.DataFrame) -> int:
    """Replace the table with these rows, in one transaction (it's reference
    data: nothing keyed in, so dropped and rebuilt rather than merged)."""
    cols = [c for c, _ in COLUMNS]
    conn, ph = db._connect()
    try:
        cur = conn.cursor()
        cur.execute(f"DROP TABLE IF EXISTS {TABLE}")
        cur.execute(f"CREATE TABLE {TABLE} (" + ", ".join(f"{c} {t}" for c, t in COLUMNS) + ")")
        cur.executemany(f"INSERT INTO {TABLE} ({', '.join(cols)}) VALUES ({', '.join([ph] * len(cols))})",
                        [tuple(db._clean(v) for v in row)
                         for row in frame[cols].itertuples(index=False)])
        conn.commit()
        return len(frame)
    finally:
        conn.close()


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def load() -> pd.DataFrame:
    """The table, or an empty frame where it isn't loaded."""
    cols = [c for c, _ in COLUMNS]
    conn, _ = db._connect()
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT {', '.join(cols)} FROM {TABLE}")
        except Exception as exc:
            if "does not exist" in str(exc).lower() or "no such table" in str(exc).lower():
                return pd.DataFrame(columns=cols)
            raise
        return pd.DataFrame(cur.fetchall(), columns=cols)
    finally:
        conn.close()
