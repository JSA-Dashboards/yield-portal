"""
Checks for the missing-county baselines (baselines.py) and how analysis.attach
labels them.

    python tests/test_baselines.py

A made-up row of five counties along one latitude, each bordering the next:
  D - C - A - B - E      (0.2 degrees of longitude apart, about 17 km)
All numbers are MADE UP.
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import pandas as pd  # noqa: E402

import analysis  # noqa: E402
import baselines as B  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


geo = B.Geo(pd.DataFrame([
    ("99001", "ZZ", "A", 40.0, -90.0, "99002 99003"),
    ("99002", "ZZ", "B", 40.0, -89.8, "99001 99005"),
    ("99003", "ZZ", "C", 40.0, -90.2, "99001 99004"),
    ("99004", "ZZ", "D", 40.0, -90.4, "99003"),
    ("99005", "ZZ", "E", 40.0, -89.6, "99002"),
], columns=["fips", "state", "name", "lat", "lon", "neighbors"]))

# --- neighbors ---------------------------------------------------------------------------
v, n = B.neighbor_value(geo, {"99002": 200, "99003": 210, "99005": 220}, "99001")
check("two in the first ring is under 3, so the second ring joins; E is twice as far, "
      "so half the weight: (2x200 + 2x210 + 220) / 5", (round(v, 1), n), (208.0, 3))
v, n = B.neighbor_value(geo, {"99002": 200, "99004": 230, "99005": 220}, "99003")
check("around C: D in ring 1, B in ring 2, E in ring 3, weighted 1/17 : 1/34 : 1/51 km "
      "= 6 : 3 : 2", (round(v, 1), n), (round((6 * 230 + 3 * 200 + 2 * 220) / 11, 1), 3))
v, n = B.neighbor_value(geo, {"99005": 220}, "99001")
check("one neighbor with the figure is not enough", (v, n), (None, 1))
check("no geography, no neighbor step", B.neighbor_value(B.Geo(), {"99002": 1}, "99001"), (None, 0))

# --- own trend --------------------------------------------------------------------------
finals = {y: 180 + 2 * (y - 2015) for y in range(2015, 2024)}
finals[2025] = 200
check("a one-year gap: the county's own line (+2 a year)", round(B.own_trend(finals, 2024), 6), 198.0)
check("no final the year before or after: not a gap, no trend",
      B.own_trend({2015: 180, 2016: 182, 2017: 184, 2018: 186, 2019: 188}, 2025), None)
check("under 5 finals: no trend", B.own_trend({2022: 190, 2023: 192, 2025: 196}, 2024), None)

# --- the chain ---------------------------------------------------------------------------
rows = [("Corn", "99001", y, finals.get(y), finals.get(y - 1), 190.0) for y in range(2016, 2026)]
rows += [("Corn", f, 2025, None, ly, avg5) for f, ly, avg5 in
         (("99002", 200.0, 195.0), ("99003", 210.0, 205.0), ("99005", 220.0, 215.0))]
cb = B.CountyBaselines(pd.DataFrame(rows, columns=["crop", "fips", "year", "final", "ly", "avg5"]), geo)
check("the county's own figure first", cb.get("Corn", "99001", 2025, "avg5"), (190.0, "county"))
v, src = cb.get("Corn", "99001", 2024, "final")
check("a gap in a county NASS publishes: its own trend", (round(v, 6), src), (198.0, "own trend"))
v, src = cb.get("Corn", "99004", 2025, "avg5")
check("a county NASS skipped: the counties around it", src, "neighbors")
check("no other crop is borrowed", cb.get("Soybeans", "99004", 2025, "avg5"), (None, None))
v, src = cb.combined("Corn", ["99001", "99004"], 2025, "avg5")
check("two counties named: their mean, tagged with the weaker source", src, "neighbors")

# --- labels in the analysis ---------------------------------------------------------------
reports = pd.DataFrame([
    dict(crop="Corn", crop_year=2025, state="ZZ", county_method="exact", county_fips=["99004"],
         yield_bpa=210.0, ly_yield=None, aph=None),
    dict(crop="Corn", crop_year=2025, state="ZZ", county_method=None, county_fips=[],
         yield_bpa=200.0, ly_yield=None, aph=None),
    dict(crop="Corn", crop_year=2025, state="ZZ", county_method="exact", county_fips=["99999"],
         yield_bpa=400.0, ly_yield=None, aph=None),
])
state_tbl = pd.DataFrame([dict(crop="Corn", state="ZZ", year=2025, final=None, current=201.0,
                               current_label="Sep forecast", ly=199.0, avg5=196.0)])
out = analysis.attach(reports, pd.DataFrame(rows, columns=["crop", "fips", "year", "final", "ly",
                                                           "avg5"]), state_tbl, pd.DataFrame(
    [("99001", "ZZ", "A", 40.0, -90.0, "99002 99003"), ("99002", "ZZ", "B", 40.0, -89.8, "99001 99005"),
     ("99003", "ZZ", "C", 40.0, -90.2, "99001 99004"), ("99004", "ZZ", "D", 40.0, -90.4, "99003"),
     ("99005", "ZZ", "E", 40.0, -89.6, "99002")],
    columns=["fips", "state", "name", "lat", "lon", "neighbors"]))
check("labels: a skipped county from its neighbors, a town from its state, a county nothing "
      "covers as a flagged state fallback",
      out["avg5_level"].tolist(), ["neighbors", "state", "state fallback"])
s = analysis.summarize(out)
check("the state fallback stays out of the headline median", (s["n_r_avg5"], round(s["r_avg5"], 4)),
      (2, round(pd.Series([210.0 / out.loc[0, "base_avg5"], 200.0 / 196.0]).median(), 4)))

if failures:
    print(f"FAILED {len(failures)}:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("All baseline checks pass.")
