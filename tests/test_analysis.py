"""
Checks for county matching, the NASS baselines and the report ratios.

    python tests/test_analysis.py

All numbers below are MADE UP (the repo is public).
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import pandas as pd  # noqa: E402

import analysis  # noqa: E402
import nass  # noqa: E402
import places  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# --- which county a place is --------------------------------------------------------------
INDEX = {"IL": {"VERMILION": "17183", "MCLEAN": "17113", "PEORIA": "17143",
                "MOULTRIE": "17139", "COLES": "17029"},
         "MO": {"STE GENEVIEVE": "29186"}}


def m(loc, st, decisions=None):
    method, names, fips, _ = places.match(loc, st, INDEX, decisions or {})
    return method, names


check("a named county is used straight away", m("Northern Vermilion County", "IL"),
      ("exact", ["VERMILION"]))
check("'McLean Co' finds MCLEAN", m("McLean Co", "IL"), ("exact", ["MCLEAN"]))
check("several counties", m("Moultrie/Coles Co", "IL"), ("several", ["MOULTRIE", "COLES"]))
check("a near-spelling waits for a person", m("Vermillion Co", "IL"), ("suggested", ["VERMILION"]))
check("'St. Genevieve Co' -> Ste Genevieve (suggested)", m("St. Genevieve Co", "MO"),
      ("suggested", ["STE GENEVIEVE"]))
check("a bare town that shares a county's name waits too", m("Peoria", "IL"),
      ("suggested", ["PEORIA"]))
check("a town is not looked up", m("Mankato", "MN"), ("none", []))
check("a region is not a county", m("Central", "IL"), ("none", []))
key = places.place_key("Peoria")
check("a confirmed match is used", m("Peoria", "IL", {("IL", key): {
    "decision": "confirmed", "counties": "PEORIA", "fips": "17143"}}), ("confirmed", ["PEORIA"]))
check("a rejected one falls back to the state", m("Peoria", "IL", {("IL", key): {
    "decision": "rejected", "counties": None, "fips": None}}), ("none", []))
check("county names for people", [places.pretty(n) for n in ("MCLEAN", "ST CLAIR", "DE KALB")],
      ["McLean", "St. Clair", "De Kalb"])

# --- NASS baselines ------------------------------------------------------------------------
rows = [("Corn", y, "IL", "YEAR", v, f"{y + 1}-01-12 12:00:00.000")
        for y, v in zip(range(2019, 2026), [180, 190, 200, 210, 220, 230, 240])]
rows += [("Corn", 2026, "IL", "YEAR - AUG FORECAST", 236.0, "2026-08-12"),
         ("Corn", 2026, "IL", "YEAR - SEP FORECAST", 238.0, "2026-09-11"),
         ("Corn", 2026, "IL", "YEAR", 238.0, "2026-09-11 12:00:00.000")]   # = the forecast
st_tbl = nass.state_table(pd.DataFrame(rows, columns=["crop", "year", "state", "period", "yield",
                                                       "loaded"]))
r26 = st_tbl[st_tbl["year"] == 2026].iloc[0]
check("the season's 'YEAR' row loaded in September is not a final", pd.isna(r26["final"]), True)
check("USDA's number in-season is the latest forecast", (r26["current"], r26["current_label"]),
      (238.0, "Sep forecast"))
check("last season's final", r26["ly"], 240.0)
check("5-season average (2021-25)", r26["avg5"], 220.0)
r25 = st_tbl[st_tbl["year"] == 2025].iloc[0]
check("a past season: final, and the final is USDA's number", (r25["final"], r25["current_label"]),
      (240.0, "final"))
check("an average needs 3+ earlier seasons (2022: 2019-21)",
      st_tbl[st_tbl["year"] == 2022].iloc[0]["avg5"], 190.0)
check("...and is missing with fewer (2021: only 2019-20)",
      pd.isna(st_tbl[st_tbl["year"] == 2021].iloc[0]["avg5"]), True)

cty = pd.DataFrame([("Corn", y, "IL", "PEORIA", "17143", v)
                    for y, v in zip(range(2021, 2026), [200, 210, 220, 230, 240])],
                   columns=["crop", "year", "state", "county", "fips", "yield"])
c_tbl = nass.county_table(cty)
c26 = c_tbl[(c_tbl["year"] == 2026)].iloc[0]
check("county: last season and 5-season average carry into the open season",
      (pd.isna(c26["final"]), c26["ly"], c26["avg5"]), (True, 240.0, 220.0))

# --- ratios --------------------------------------------------------------------------------
reports = pd.DataFrame([
    dict(crop="Corn", crop_year=2026, state="IL", yield_bpa=242.0, ly_yield=220.0, aph=None,
         county_method="exact", county_fips=["17143"], county_names=["PEORIA"]),
    dict(crop="Corn", crop_year=2026, state="IL", yield_bpa=257.0, ly_yield=None, aph=None,
         county_method="none", county_fips=[], county_names=[]),
])
rep = analysis.attach(reports, c_tbl, st_tbl)
check("county baseline when the county is known", (rep.loc[0, "avg5_level"], rep.loc[0, "r_avg5"]),
      ("county", 1.1))
check("state baseline otherwise", (rep.loc[1, "avg5_level"], round(rep.loc[1, "r_avg5"], 4)),
      ("state", round(257 / 220, 4)))
check("vs USDA uses the state's in-season forecast", round(rep.loc[0, "r_usda"], 4),
      round(242 / 238, 4))
check("same field vs its own last year", round(rep.loc[0, "pair_ly_pct"], 1), 10.0)
check("confidence labels", [analysis.tier(n) for n in (1, 3, 9, 10)],
      ["Too few", "Directional", "Directional", "Firmer"])
pulled = analysis.counties(rep.assign(county_names=[["PEORIA"], []]), state_median=1.20)
check("one report is pulled most of the way to the state: (1x1.10 + 3x1.20)/4",
      round(pulled.loc[0, "pulled"], 4), round((1.10 + 3 * 1.20) / 4, 4))

# --- the headline tiles (Explore and the weekly email) ------------------------------------
import data  # noqa: E402

tiles = pd.DataFrame({
    "crop_year": [2026, 2026, 2026, 2025, 2025],
    "yield_bpa": [220.0, 240.0, 260.0, 200.0, 220.0],
    "ly_yield": [200.0, None, 250.0, None, None],
    "aph": [None, 200.0, None, None, None],
    "disease": ["Tar spot", "Tar spot, Hail", None, "Hail", None],
})
tiles["vs_ly"] = tiles["yield_bpa"] - tiles["ly_yield"]
tiles["vs_aph"] = tiles["yield_bpa"] - tiles["aph"]
h = data.headline(tiles, 2026, 2025)
check("tiles: count, average and its change in bpa and %",
      (h["reports"], h["avg"], h["avg_change"], round(h["avg_change_pct"], 2)),
      (3, 240.0, 30.0, round((240 / 210 - 1) * 100, 2)))
check("vs LY: mean gain over the reports giving both, % of their LY bushels",
      (h["vs_ly"], round(h["vs_ly_pct"], 2), h["vs_ly_n"]), (15.0, round(30 / 450 * 100, 2), 2))
check("vs APH", (h["vs_aph"], h["vs_aph_pct"], h["vs_aph_n"]), (40.0, 20.0, 1))
check("most-cited damage in the latest year", h["damage"], ("Tar spot", 2))
first = data.headline(tiles, 2025)
check("no earlier year: no change", (first["avg_change"] != first["avg_change"],
                                     first["vs_aph_n"]), (True, 0))

if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("All analysis checks pass.")
