"""
Checks for reading ISA strip-trial reports (isa_trials.py).

    python tests/test_isa_trials.py

Every report fragment here is MADE UP: each one copies only the labels a layout
is recognised by, with invented treatments and yields.
"""
import csv
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import pandas as pd  # noqa: E402

import isa_trials as I  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def text(*lines):
    return "\n".join(str(x) for x in lines)


# (label, crop, trial detail, response, report text, expected yields, expected layout)
CASES = [
    ("2023: treatment, yield, letter", "Corn", "Product vs Untreated", 2.0,
     text("Treatment", "Yield", "Yield Group", "Untreated", 231, "a", "Product", 233, "a",
          "Table 2"), [231.0, 233.0], "2023"),
    ("2021: after 'Average for Treatments (bu/ac)'", "Soybeans", "Product vs Untreated", -3.5,
     text("Yield Average for", "Treatments (bu/ac)", 51.4, 54.9, "Table 2"), [51.4, 54.9], "2021"),
    ("2015: 'Yield Difference | a | b | d | Yield Average for All'", "Soybeans", "A vs B", 1.1,
     text("Yield Difference", 58.1, 57.0, 1.1, "Yield Average for All"), [58.1, 57.0], "2015"),
    ("2018: three treatments before 'Yield Average for All'", "Corn", "A vs B vs C", 5.5,
     text("Strip", 210.0, 215.5, 214.0, "Yield Average for All"), [210.0, 215.5, 214.0], "2018"),
    ("2013: before 'Yield Difference | d'", "Corn", "Product vs Untreated", 4.2,
     text("Product", "Untreated", 180.2, 176.0, "Yield Difference", 4.2, "Notes"),
     [180.2, 176.0], "2013"),
    ("2010: the difference closes the run", "Corn", "29K vs 35K", 1.5,
     text(190.0, 188.5, 1.5, "Percent of Trial", "Yield (Bu/Ac)", "Yield", "Difference",
          "Yield By Treatment and Soil Type", "YIELD AVERAGE FOR TRIAL"), [190.0, 188.5], "2010"),
    ("2006: after 'YIELD AVERAGE FOR TRIAL'", "Soybeans", "Product vs Untreated", 1.5,
     text("YIELD AVERAGE FOR TRIAL", 55.5, 54.0, "Notes"), [55.5, 54.0], "2006"),
    ("2005: before 'Yield Difference | Yield Average'", "Corn", "N150 vs N100", -0.9,
     text(170.1, 171.0, "Yield Difference", "Yield Average (Bu/a)"), [170.1, 171.0], "2005"),
    ("2005b: three before 'Yield Average (Bu/a)'", "Corn", "N160 vs N130 vs N70", -1.5,
     text("160 lbs", "130 lbs", "70 lbs", 190.0, 191.5, 180.0, "Yield Average (Bu/a)",
          "10.0 Bu/a"), [190.0, 191.5, 180.0], "2005b"),
    ("2005c: after 'Yield Average (Bu/a)'", "Corn", "Product vs Untreated", 0.8,
     text("Treated", "Untreated", "Yield Average (Bu/a)", 195.0, 194.2, "Yield Difference",
          "0.8 Bu/a"), [195.0, 194.2], "2005c"),
    ("2012w: the 'Avg' rows under a table of reps", "Soybeans", "Program vs Check", 0.4,
     text("Rep Treatment", "Yield (bu/a)", "Foxtail Waterhemp Total", "1 Program", 52.0, 0.0,
          "1 Check", 51.0, 0.2, "Avg Program", 51.5, 0.0, "Avg Check", 51.1, 0.1,
          "Paired TTEST", 0.5), [51.5, 51.1], "2012w"),
]
for label, crop, detail, resp, report, want, layout in CASES:
    check(label, I.parse(report, crop, detail, resp), (want, layout))

two = text("Yield Difference", 58.1, 57.0, 1.1, "Yield Average for All")
check("the list says three, the report compared two: the response settles it",
      I.parse(two, "Soybeans", "A vs B vs C", 1.1), ([58.1, 57.0], "2015"))
check("...but with no response to check, only the list's count is tried",
      I.parse(two, "Soybeans", "A vs B vs C", None), ([], None))
check("a reading that disagrees with ISA's response is not taken",
      I.parse(two, "Soybeans", "A vs B", 3.0), ([], None))
check("a 'soybean' trial with corn-level yields stays unread",
      I.parse(text("Yield Average for", "Treatments (bu/ac)", 148.6, 151.9, "Table 2"),
              "Soybeans", "Cover Crop vs Untreated", -3.3), ([], None))
whole = text("Treatment", "Yield", "Yield Group", "Product", 263, "a", "Untreated", 260, "a",
             "Table 2")
check("whole bushels can be a bushel off the listed response",
      I.parse(whole, "Corn", "Product vs Untreated", 3.4), ([263.0, 260.0], "2023"))
check("...but not two", I.parse(whole, "Corn", "Product vs Untreated", 4.5), ([], None))

check("agrees: any pair of three", I.agrees([213.4, 222.2, 220.6], 8.8), True)
check("agrees: the sign doesn't matter", I.agrees([224.5, 226.7], 2.2), True)
check("agrees: no response, nothing to check", I.agrees([60.0, 61.0], None), True)
check("treatments: 'A vs. B vs C'", I.n_treatments("A vs. B vs C"), 3)
check("treatments: nothing to split is still a comparison of two", I.n_treatments(""), 2)

# NASS beside each trial: O'Brien is NASS's "O BRIEN"; the cache has counties from 2015
county_raw = pd.DataFrame(
    [("Corn", 2020, "IA", "O BRIEN", "19141", 200.0), ("Corn", 2020, "IA", "STORY", "19169", 210.0),
     ("Corn", 2020, "IL", "STORY", "17999", 150.0)],
    columns=["crop", "year", "state", "county", "fips", "yield"])
state_tbl = pd.DataFrame([("Corn", "IA", 2020, 180.0), ("Corn", "IA", 2010, 165.0)],
                         columns=["crop", "state", "year", "final"])
tr = pd.DataFrame([
    dict(trial_id="T1", year=2020, crop="Corn", county="O'Brien", trial_yield=220.0, status="read"),
    dict(trial_id="T2", year=2020, crop="Corn", county="Story", trial_yield=231.0, status="read"),
    dict(trial_id="T3", year=2010, crop="Corn", county="Story", trial_yield=181.5, status="read"),
    dict(trial_id="T4", year=2020, crop="Corn", county="Story", trial_yield=None, status="unread"),
])
w = I.with_nass(tr, county_raw, state_tbl).set_index("trial_id")
check("O'Brien finds NASS's O BRIEN", w.loc["T1", "county_final"], 200.0)
check("an Iowa county isn't matched to another state's", w.loc["T2", "county_final"], 210.0)
check("field over county", round(w.loc["T1", "vs_county"], 6), 0.1)
check("before the cached counties: state only", (pd.isna(w.loc["T3", "county_final"]),
                                                 round(w.loc["T3", "vs_state"], 6)), (True, 0.1))
s = I.by_season(w.reset_index()).set_index("year")
check("by season: read trials only, mean field, mean county, median ratio",
      (int(s.loc[2020, "trials"]), s.loc[2020, "field"], s.loc[2020, "county"],
       round(s.loc[2020, "over_county"], 6)), (2, 225.5, 205.0, 0.1))

# the cache -> rows: read, unread, and a trial whose report never arrived
with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(tmp)
    (tmp / "text").mkdir()
    with open(tmp / "trials.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, ["year", "landform", "district", "county", "crop", "trial_type",
                                "trial_detail", "avg_response", "trial_id", "report_url"])
        wr.writeheader()
        for tid, detail, resp in (("ST1", "A vs B vs C", "1.1"), ("ST2", "A vs B", "9.9"),
                                  ("ST3", "A vs B", "1.0")):
            wr.writerow(dict(year=2016, landform="Plain", district="5 (Central)", county="Story",
                             crop="Soybean", trial_type="Fungicide - vs untreated",
                             trial_detail=detail, avg_response=resp, trial_id=tid,
                             report_url=f"https://example.org/{tid}.pdf"))
    (tmp / "text" / "ST1.txt").write_text(two, encoding="utf-8")
    (tmp / "text" / "ST2.txt").write_text(two, encoding="utf-8")
    got = {r["trial_id"]: r for r in I.rows_from_cache(tmp / "trials.csv", tmp / "text")}
check("cache: read, unread, no report", [got[t]["status"] for t in ("ST1", "ST2", "ST3")],
      ["read", "unread", "no report"])
check("cache: treatments counted from the reading, crop named as the portal does",
      (got["ST1"]["treatments"], got["ST1"]["crop"], round(got["ST1"]["trial_yield"], 6),
       got["ST1"]["yields"]), (2, "Soybeans", 57.55, "58.1 / 57"))

if failures:
    print(f"FAILED {len(failures)}:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("All ISA strip-trial checks pass.")
