"""
Checks for reading ISA strip-trial reports (isa_trials.py).

    python tests/test_isa_trials.py

Every report fragment here is MADE UP: each one copies only the labels a layout
is recognised by, with invented treatments and yields.
"""
import csv
import pathlib
import re
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
    ("2023: the second table in a report, for its 'B' trial", "Soybeans", "Spectra vs Untreated",
     12.0, text("Treatment", "Yield", "Yield Group", "Fungicide", 71.2, "a", "Untreated", 67.5,
                "a", "Table 2: notes", "Treatment", "Yield", "Yield Group", "Spectra", 57.3, "a",
                "Untreated", 45.3, "a", "Table 3: notes"), [57.3, 45.3], "2023"),
    ("2021: after 'Average for Treatments (bu/ac)'", "Soybeans", "Product vs Untreated", -3.5,
     text("Yield Average for", "Treatments (bu/ac)", 51.4, 54.9, "Table 2"), [51.4, 54.9], "2021"),
    ("2021: the label split over cells, four treatments where the list names two", "Corn",
     "Pacesetter vs Untreated", 20.9,
     text("Yield Average", "for Treatments", "(bu/ac)", 211.2, 216.2, 207.2, 195.3, "Table 2"),
     [211.2, 216.2, 207.2, 195.3], "2021"),
    ("2021: significance letters on the values", "Corn", "A vs B vs C", 4.0,
     text("Yield Average", "for Treatments", "(bu/ac)", "235.82 a", "239.54 a", "239.76 a",
          "(LSD0.10) = 11.66 bu/acre"), [235.82, 239.54, 239.76], "2021"),
    ("2015: 'Yield Difference | a | b | d | Yield Average for All'", "Soybeans", "A vs B", 1.1,
     text("Yield Difference", 58.1, 57.0, 1.1, "Yield Average for All"), [58.1, 57.0], "2015"),
    ("2018: three treatments before 'Yield Average for All'", "Corn", "A vs B vs C", 5.5,
     text("Strip", 210.0, 215.5, 214.0, "Yield Average for All"), [210.0, 215.5, 214.0], "2018"),
    ("2018: five N rates where the list names two of them", "Corn", "N190 vs N160", -5.0,
     text("190 lbs N", "160 lbs N", "192.3 a", "189.5 ab", "185.9 b", "180.3 c", "185.3 bc",
          "Treatments with the same letter are not statistically significant"),
     [192.3, 189.5, 185.9, 180.3, 185.3], "2018L"),
    ("2018: broken font spacing ('A ran do m izatio n')", "Corn", "Coulter vs Y-Drop vs Y-Drop",
     -10.9, text("Y-Drop", 209.6, 205.2, 198.7, "A ran do m izatio n test sug g ested"),
     [209.6, 205.2, 198.7], "2018"),
    ("2013: before 'Yield Difference | d'", "Corn", "Product vs Untreated", 4.2,
     text("Product", "Untreated", 180.2, 176.0, "Yield Difference", 4.2, "Notes"),
     [180.2, 176.0], "2013"),
    ("2013: broken font spacing ('Yield Differen ce')", "Soybeans", "Product vs Untreated", -3.5,
     text("Un treated", 50.7, 54.2, "Yield Differen ce", -3.5, "A ran dom ization test"),
     [50.7, 54.2], "2013"),
    ("2010: the difference closes the run", "Corn", "29K vs 35K", 1.5,
     text(190.0, 188.5, 1.5, "Percent of Trial", "Yield (Bu/Ac)", "Yield", "Difference",
          "Yield By Treatment and Soil Type", "YIELD AVERAGE FOR TRIAL"), [190.0, 188.5], "2010"),
    ("2010: ...even when it's a plausible corn yield itself", "Corn", "Manure and N vs Manure",
     48.2, text("Harps Loam", 95, 4.7, 4.6, 239.2, 189.6, 49.6, 238.2, 190.0, 48.2,
                "Percent of Trial", "Yield (Bu/Ac)", "Yield", "Difference",
                "Yield By Treatment and Soil Type", "YIELD AVERAGE FOR TRIAL"),
     [238.2, 190.0], "2010"),
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
    ("2015b: all four before 'Yield Averages', not three of them", "Corn",
     "Commander vs Fixed Rate", 0.4,
     text("Fixed Rate", 241.6, 241.5, 239.1, 241.2, "Yield Averages", "(bu/acre)",
          "Yield differences are"), [241.6, 241.5, 239.1, 241.2], "2015b"),
    ("2012w: the 'Avg' rows under a table of reps", "Soybeans", "Program vs Check", 0.4,
     text("Rep Treatment", "Yield (bu/a)", "Foxtail Waterhemp Total", "1 Program", 52.0, 0.0,
          "1 Check", 51.0, 0.2, "Avg Program", 51.5, 0.0, "Avg Check", 51.1, 0.1,
          "Paired TTEST", 0.5), [51.5, 51.1], "2012w"),
    ("2012w: 'AVG', and no t-test row after", "Soybeans", "Program vs Check", 0.4,
     text("6 Check", 46.5, 1, "AVG Program High", 51.6, 0.5, "Avg Check", 51.2, 1.7, "ST2012"),
     [51.6, 51.2], "2012w"),
]
for label, crop, detail, resp, report, want, layout in CASES:
    check(label, I.parse(report, crop, detail, resp), (want, layout, True))

two = text("Yield Difference", 58.1, 57.0, 1.1, "Yield Average for All")
check("the list says three, the report compared two: the response settles it",
      I.parse(two, "Soybeans", "A vs B vs C", 1.1), ([58.1, 57.0], "2015", True))
check("...but with no response to check, only the list's count is taken",
      I.parse(two, "Soybeans", "A vs B vs C", None), ([], None, False))
check("a reading that disagrees with ISA's response is not taken",
      I.parse(two, "Soybeans", "A vs B", 3.0), ([], None, False))
check("a 'soybean' trial with corn-level yields stays unread",
      I.parse(text("Yield Average for", "Treatments (bu/ac)", 148.6, 151.9, "Table 2"),
              "Soybeans", "Cover Crop vs Untreated", -3.3), ([], None, False))
whole = text("Treatment", "Yield", "Yield Group", "Product", 263, "a", "Untreated", 260, "a",
             "Table 2")
check("whole bushels can be a bushel off the listed response",
      I.parse(whole, "Corn", "Product vs Untreated", 3.4), ([263.0, 260.0], "2023", True))
check("...but not two", I.parse(whole, "Corn", "Product vs Untreated", 4.5), ([], None, False))
rates = text("80 lbs N", "200 lbs N", "125.9 d", "151.1 c", "177.9 b", "193.8 a", "196.0 a",
             "Treatments with the same letter are not statistically significant")
check("five rates, five listed, ISA's response the LSD: taken, unverified",
      I.parse(rates, "Corn", "N200 vs N170 vs N140 vs N110 vs N80", 6.2),
      ([125.9, 151.1, 177.9, 193.8, 196.0], "2018L", False))
check("...but never two treatments that disagree",
      I.parse(text("Strip", 210.0, 215.5, "Yield Average for All"), "Corn", "A vs B", 9.9),
      ([], None, False))
check("...nor a run with more numbers in it than the treatments",
      I.parse(text(12, 31, 210.0, 215.5, 214.0, "Yield Average for All"), "Corn", "A vs B vs C",
              9.9), ([], None, False))

beans = text("Crop Rotation", "Soybeans Following Corn", "Yield Difference", 57.7, 57.2, 0.5,
             "Yield Average for All")
check("listed as corn, the report says soybeans, ~57 bu: soybeans",
      I.settle_crop(beans, "Corn", "Cover Crop vs No Cover Crop", 0.5),
      ("Soybeans", ([57.7, 57.2], "2015", True)))
check("the report says soybeans but the field made 190 bu: the list's corn stands",
      I.settle_crop(text("Crop Rotation", "Soybeans Following Corn", "Yield Difference", 194.2,
                         185.0, 9.2, "Yield Average for All"), "Corn", "A vs B", 9.2),
      ("Corn", ([194.2, 185.0], "2015", True)))
check("listed as soybeans, unread at soybean range, the report and yields say corn",
      I.settle_crop(text("on a corn following soybeans rotation", "Yield Difference", 150.5, 149.1,
                         1.4, "Yield Average for All"), "Soybeans", "A vs B", 1.4),
      ("Corn", ([150.5, 149.1], "2015", True)))
check("no rotation line: the list's crop", I.report_crop(two), None)

check("agrees: any pair of three", I.agrees([213.4, 222.2, 220.6], 8.8), True)
check("agrees: the sign doesn't matter", I.agrees([224.5, 226.7], 2.2), True)
check("agrees: no response, nothing to check", I.agrees([60.0, 61.0], None), True)
check("treatments: 'A vs. B vs C'", I.n_treatments("A vs. B vs C"), 3)
check("treatments: nothing to split is still a comparison of two", I.n_treatments(""), 2)
check("labels tolerate stray spaces",
      bool(re.fullmatch(I.L("Yield Difference"), "Yield Differen ce")), True)

# NASS beside each trial: O'Brien is NASS's "O BRIEN"; a county NASS skipped has only Iowa
county_raw = pd.DataFrame(
    [("Corn", 2020, "IA", "O BRIEN", "19141", 200.0), ("Corn", 2020, "IA", "STORY", "19169", 210.0),
     ("Corn", 2020, "IL", "STORY", "17999", 150.0)],
    columns=["crop", "year", "state", "county", "fips", "yield"])
state_tbl = pd.DataFrame([("Corn", "IA", 2020, 180.0), ("Corn", "IA", 2010, 165.0)],
                         columns=["crop", "state", "year", "final"])
tr = pd.DataFrame([
    dict(trial_id="T1", year=2020, crop="Corn", county="O'Brien", trial_yield=220.0, status="read",
         treatments=2, field_id="f1"),
    dict(trial_id="T2", year=2020, crop="Corn", county="Story", trial_yield=231.0, status="read",
         treatments=4, field_id="f2"),
    dict(trial_id="T2b", year=2020, crop="Corn", county="Story", trial_yield=236.0, status="read",
         treatments=2, field_id="f2"),          # the same field, another comparison
    dict(trial_id="T3", year=2010, crop="Corn", county="Story", trial_yield=181.5, status="read",
         treatments=2, field_id="f3"),
    dict(trial_id="T4", year=2020, crop="Corn", county="Story", trial_yield=None, status="unread",
         treatments=2, field_id="f4"),
])
w = I.with_nass(tr, county_raw, state_tbl).set_index("trial_id")
check("O'Brien finds NASS's O BRIEN", w.loc["T1", "county_final"], 200.0)
check("an Iowa county isn't matched to another state's", w.loc["T2", "county_final"], 210.0)
check("field over county", round(w.loc["T1", "vs_county"], 6), 0.1)
check("no county row that season: Iowa only", (pd.isna(w.loc["T3", "county_final"]),
                                                 round(w.loc["T3", "vs_state"], 6)), (True, 0.1))
check("fields: each once, the reading with the most treatments",
      sorted(I.fields(w.reset_index()).trial_id), ["T1", "T2", "T3"])
s = I.by_season(w.reset_index()).set_index("year")
check("by season: fields read, mean field, mean county, median ratio",
      (int(s.loc[2020, "fields"]), s.loc[2020, "field"], s.loc[2020, "county"],
       round(s.loc[2020, "over_county"], 6)), (2, 225.5, 205.0, 0.1))

# the cache -> rows: read, unread, a trial whose report never arrived, and a twin
# with no report of its own (ISA's "...a" entries)
with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(tmp)
    (tmp / "text").mkdir()
    with open(tmp / "trials.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, ["year", "landform", "district", "county", "crop", "trial_type",
                                "trial_detail", "avg_response", "trial_id", "report_url"])
        wr.writeheader()
        for tid, detail, resp in (("ST1", "A vs B vs C", "1.1"), ("ST2", "A vs B", "9.9"),
                                  ("ST3", "A vs B", "1.0"), ("ST1a", "A vs B", "1.1")):
            wr.writerow(dict(year=2016, landform="Plain", district="5 (Central)", county="Story",
                             crop="Soybean", trial_type="Fungicide - vs untreated",
                             trial_detail=detail, avg_response=resp, trial_id=tid,
                             report_url=f"https://example.org/{tid}.pdf"))
    (tmp / "text" / "ST1.txt").write_text(two, encoding="utf-8")
    (tmp / "text" / "ST2.txt").write_text(two, encoding="utf-8")
    got = {r["trial_id"]: r for r in I.rows_from_cache(tmp / "trials.csv", tmp / "text")}
check("cache: read, unread, no report, same field",
      [got[t]["status"] for t in ("ST1", "ST2", "ST3", "ST1a")],
      ["read", "unread", "no report", "same field"])
check("cache: one report, one field id; the twin takes its base's",
      (got["ST1"]["field_id"] == got["ST2"]["field_id"] == got["ST1a"]["field_id"],
       got["ST3"]["field_id"]), (True, None))
check("cache: treatments counted from the reading, crop named as the portal does",
      (got["ST1"]["treatments"], got["ST1"]["crop"], round(got["ST1"]["trial_yield"], 6),
       got["ST1"]["yields"]), (2, "Soybeans", 57.55, "58.1 / 57"))

if failures:
    print(f"FAILED {len(failures)}:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("All ISA strip-trial checks pass.")
