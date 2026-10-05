"""
NASS yields for the analysis page, read from the fleet's shared cache
(JSA.NASS_CACHE, filled by the usda-nass-etl job). The portal never calls NASS:
keys are issued per person, so a shared app reads the cache instead.

Two cached query shapes cover everything. They must stay identical to the job
lists that fill them, or the cache key misses and the page goes blank:
  county yields  one query per crop and year, every county, 2005 on
                 (usda-nass-etl jobs/yield_portal.py, weekly)
  state yields   every state yield since 1980, finals and monthly forecasts
                 (usda-nass-etl jobs/domestic_production.py)

Snowflake flattens the cached payloads and returns only the fields used here:
a county year's whole payload is 1-2 MB, so the 44 county keys would be ~80 MB.

For the crop year still in progress, NASS's "YEAR" row is USDA's latest
forecast, not a final: a YEAR row counts as final only when it was loaded after
the January following that harvest.
"""
import pandas as pd
import streamlit as st

import db
import nass_cache_client as ncc

COMMODITY = {"Corn": {"commodity_desc": "CORN", "util_practice_desc": "GRAIN"},
             "Soybeans": {"commodity_desc": "SOYBEANS"}}
FIRST_YEAR = 2005                     # = usda-nass-etl jobs/yield_portal.FIRST_YEAR (ISA trials)
CACHE_TABLE = "JSA.NASS_CACHE.NASS_CACHE"
AVG_YEARS = 5                         # "normal" = average of the 5 finals before the year
MIN_AVG_YEARS = 3                     # ...from at least 3 published years
FORECAST_MONTH = {"YEAR - AUG FORECAST": ("Aug", 8), "YEAR - SEP FORECAST": ("Sep", 9),
                  "YEAR - OCT FORECAST": ("Oct", 10), "YEAR - NOV FORECAST": ("Nov", 11)}


def county_params(crop, year):
    return {"source_desc": "SURVEY", "sector_desc": "CROPS", "agg_level_desc": "COUNTY",
            "year": str(year), "statisticcat_desc": "YIELD", "unit_desc": "BU / ACRE",
            **COMMODITY[crop]}


def state_params(crop):
    return {**COMMODITY[crop], "statisticcat_desc": "YIELD", "unit_desc": "BU / ACRE",
            "source_desc": "SURVEY", "domain_desc": "TOTAL", "freq_desc": "ANNUAL",
            "agg_level_desc": "STATE", "year__GE": "1980"}


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return float("nan")             # "(D)", "(NA)": withheld


@db._retry_once
def _fetch(keys: tuple):
    """The records cached under `keys`, over the app's shared Snowflake session:
    one row per ALL PRODUCTION PRACTICES record, (cache_key, fetched_at, year,
    state_alpha, county_name, state_fips_code, county_ansi, Value,
    reference_period_desc, load_time). A key with no records still gives one
    row, all NULL past fetched_at, so it counts towards as_of."""
    conn = db.sf_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT c.cache_key, c.fetched_at, r.value:year::string, r.value:state_alpha::string, "
            "r.value:county_name::string, r.value:state_fips_code::string, "
            "r.value:county_ansi::string, r.value:Value::string, "
            "r.value:reference_period_desc::string, r.value:load_time::string "
            f"FROM {CACHE_TABLE} c, LATERAL FLATTEN(input => c.data:data, outer => TRUE) r "
            f"WHERE c.error IS NULL AND c.cache_key IN ({', '.join(['%s'] * len(keys))}) "
            "AND (r.value IS NULL "
            "OR r.value:prodn_practice_desc::string = 'ALL PRODUCTION PRACTICES')",
            keys)
        return cur.fetchall()
    finally:
        conn.close()


@st.cache_data(ttl=6 * 3600, show_spinner="Loading NASS yields…")
def load(through_year: int):
    """-> (county, state, as_of). county: crop, year, state, county, fips, yield.
    state: crop, year, state, period, yield, loaded. Empty frames when the portal
    isn't on Snowflake (local SQLite has no cache)."""
    county_cols = ["crop", "year", "state", "county", "fips", "yield"]
    state_cols = ["crop", "year", "state", "period", "yield", "loaded"]
    if not db.use_snowflake():
        return pd.DataFrame(columns=county_cols), pd.DataFrame(columns=state_cols), None
    wanted = {}
    for crop in COMMODITY:
        wanted[ncc._cache_key("api_GET", state_params(crop))] = ("state", crop)
        for y in range(FIRST_YEAR, through_year + 1):
            wanted[ncc._cache_key("api_GET", county_params(crop, y))] = ("county", crop)
    county, state, as_of = [], [], None
    for key, fetched, year, st_, name, st_fips, ansi, value, period, loaded in _fetch(tuple(wanted)):
        as_of = max(as_of, fetched) if as_of else fetched
        if year is None:                    # a cached key with no records
            continue
        kind, crop = wanted[key]
        if kind == "county":
            if not ansi or "OTHER" in (name or ""):
                continue
            county.append((crop, int(year), st_, name, st_fips + ansi, _num(value)))
        elif st_ not in ("US", "OT"):
            state.append((crop, int(year), st_, period, _num(value), loaded or ""))
    return (pd.DataFrame(county, columns=county_cols).dropna(subset=["yield"]),
            pd.DataFrame(state, columns=state_cols).dropna(subset=["yield"]), as_of)


def _rolling(finals: pd.DataFrame, keys: list) -> pd.DataFrame:
    """finals (keys + year + final) -> + ly and avg5 for every year that has
    either: last year's final, and the mean of the 5 finals before (3+ needed)."""
    out = []
    for k, g in finals.groupby(keys):
        f = dict(zip(g["year"], g["final"]))
        for y in range(min(f), max(f) + 2):
            prior = [f[p] for p in range(y - AVG_YEARS, y) if p in f]
            out.append((*((k,) if len(keys) == 1 else k), y, f.get(y), f.get(y - 1),
                        sum(prior) / len(prior) if len(prior) >= MIN_AVG_YEARS else None))
    return pd.DataFrame(out, columns=keys + ["year", "final", "ly", "avg5"])


def state_table(state: pd.DataFrame) -> pd.DataFrame:
    """crop, state, year -> final (None while the season is open), ly, avg5,
    current (final, else USDA's latest forecast) and current_label."""
    rows = []
    for (crop, st_, year), g in state.groupby(["crop", "state", "year"]):
        yr = g[g["period"] == "YEAR"]
        final = (float(yr["yield"].iloc[0])
                 if len(yr) and str(yr["loaded"].iloc[0]) >= f"{year + 1}-01-01" else None)
        fc = g[g["period"].isin(FORECAST_MONTH)].assign(
            m=lambda d: d["period"].map(lambda p: FORECAST_MONTH[p][1])).sort_values("m")
        if final is not None:
            current, label = final, "final"
        elif len(fc):
            current, label = float(fc["yield"].iloc[-1]), FORECAST_MONTH[fc["period"].iloc[-1]][0] + " forecast"
        elif len(yr):
            current, label = float(yr["yield"].iloc[0]), "forecast"
        else:
            continue
        rows.append((crop, st_, year, final, current, label))
    t = pd.DataFrame(rows, columns=["crop", "state", "year", "final", "current", "current_label"])
    roll = _rolling(t.dropna(subset=["final"])[["crop", "state", "year", "final"]],
                    ["crop", "state"]).drop(columns="final")
    return t.merge(roll, on=["crop", "state", "year"], how="left")


def county_table(county: pd.DataFrame) -> pd.DataFrame:
    """crop, fips, year -> final, ly, avg5 (county finals; none in-season)."""
    finals = county.rename(columns={"yield": "final"})[["crop", "fips", "year", "final"]]
    return _rolling(finals, ["crop", "fips"])


def county_index(county: pd.DataFrame) -> dict:
    """{state: {county name as NASS spells it: fips}} over every year cached."""
    idx = {}
    for st_, name, fips in county[["state", "county", "fips"]].drop_duplicates().itertuples(index=False):
        idx.setdefault(st_, {})[name] = fips
    return idx
