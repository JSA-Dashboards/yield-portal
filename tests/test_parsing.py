"""
Regression checks for the yield parser and the archive matcher.

    python tests/test_parsing.py

Run after any change to parse_pdfs.py or data.find_match. The reports below are
MADE UP, written in the shapes the real emails take (the repo is public, so no
real report text lives here). The real-data cases are in
tests/test_parsing_local.py, which is kept off the repo; this file runs it too
when it is present.
"""
import datetime as dt
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import pandas as pd  # noqa: E402

import data  # noqa: E402
import parse_pdfs as P  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# Body shaped like Outlook's .Body for these emails: a preview line with
# zero-width padding, the mail filter's banner, then the real body.
BANNER = ("External (reports@example.com <mailto:reports@example.com> )\n"
          "  Safe <https://filter.example.com/report?id=x>   Spam <https://x>\n\n")


def email(subject, report):
    preview = report.splitlines()[0] + "  ‌ " * 20
    return P.parse_email(subject, f"{preview}\n{BANNER}{report}\n", 2026, dt.date(2026, 10, 2))


rows = email("YIELD: IN soybeans",
             "Adams Co, IN: 80+ bpa, best beans this grower has raised. "
             "Better than expected by about 10 bu.")
check("preview not doubled", len(rows), 1)
check("'80+ bpa' is the yield; 'by about 10 bu' is a difference", rows[0]["yield_bpa"], 80)
check("zero-width padding stripped", "‌" in rows[0]["raw_text"], False)

rows = email("YIELD: NE MO Linn Co corn",
             "Wrapped up a small farm at 209-BPA. Easily a record. 112 a year ago. 150 average.")
check("subject county; 'NE' before 'MO' is a direction", (rows[0]["location"], rows[0]["state"]),
      ("Linn Co", "MO"))
check("'209-BPA'", rows[0]["yield_bpa"], 209)
check("'112 a year ago' is last year", rows[0]["ly_yield"], 112)

rows = email("YIELD: NC IA corn", "First field went 233 bpa on good ground.")
check("subject-only state: 'NC IA' is north-central Iowa", rows[0]["state"], "IA")

rows = email("YIELD: W TN soybeans",
             "Lauderdale county, 41.5 acres (dryland) did 68.25 bushels…grower is amazed")
check("county opens the body", (rows[0]["location"], rows[0]["state"]), ("Lauderdale county", "TN"))
check("dryland -> Non-irrigated", rows[0]["irrigation"], "Non-irrigated")
check("'68.25 bushels'", rows[0]["yield_bpa"], 68.25)

rows = email("YIELD: NC IA silage numbers",
             "Silage numbers NC IA:\nMitchell Co IA 248\nHoward Co IA 215\nHoward Co IA 236\n"
             "A lot running from 215 to 245")
check("heading dropped, one report per county line", [r["location"] for r in rows],
      ["Mitchell Co", "Howard Co", "Howard Co"])
check("bare figure after the place", [r["yield_bpa"] for r in rows], [248, 215, 236])
check("silage subject marks every report", all(r["is_silage"] for r in rows), True)

rows = email("YIELD: Iowa corn",
             "*\tSac Co, IA: 106 day corn ran 231 bpa dry, came out at 23% moisture.\n"
             "*\tCalhoun Co, IA: Wet harvest so far. Yields running above last year at 245+ bpa.")
check("bulleted list -> one report each", [(r["location"], r["yield_bpa"]) for r in rows],
      [("Sac Co", 231), ("Calhoun Co", 245)])

rows = email("YIELD: Vermilion Co IL corn (EC IL)",
             "Northern Vermilion County (east-central Illinois) - First field of corn made 262 bpa, "
             "vs 271 bpa in 2024, which was a record.\n"
             "Southern Vermilion County (east-central IL) - 35 acres of down corn harvested. "
             "Yield about 171 bpa dry.\nHoping it improves from here.")
check("split at county openers", [(r["location"], r["yield_bpa"]) for r in rows],
      [("Northern Vermilion County", 262), ("Southern Vermilion County", 171)])
check("'271 bpa in 2024' is last year", rows[0]["ly_yield"], 271)

rows = email("YIELD: Regional OH corn and silage",
             "Southwest Ohio first 40 acres of corn went 212 vs. 236 last year.\n"
             "Northern Ohio silage 22.4 tons per acre or 219.5 bpa.")
check("split at region openers", [(r["location"], r["yield_bpa"], r["is_silage"]) for r in rows],
      [("Southwest Ohio", 212, False), ("Northern Ohio", 219.5, True)])
check("'vs. 236 last year'", rows[0]["ly_yield"], 236)

rows = email("YIELD: Macon Co IL ",
             "Corn - 120 acres green snap 151 bpa. 30 acres light hail 218bpa\n"
             "Beans - Averaging 75-80bpa at or slightly above APH.")
check("split by crop opener", [(r["crop"], r["location"], r["yield_bpa"]) for r in rows],
      [("Corn", "Macon Co", 151), ("Soybeans", "Macon Co", 75)])

rows = email("RESEND: Correcting Subject YIELD: IL corn & beans (Adams & Brown Co)",
             "Adams Co IL (western) first 2 fields of beans 82-84 bpa. Pleased. Brown Co IL "
             "(western) corn avg 238. As expected, not a record and off last year about 6%")
check("two reports on one line, own crop each",
      [(r["location"], r["crop"], r["yield_bpa"]) for r in rows],
      [("Adams Co", "Soybeans", 82), ("Brown Co", "Corn", 238)])

rows = email("YIELD: Chatham, IL soybeans",
             "60 acres, 91 bu/acre\n60 acres, 83 bu/acre, both records by 8%.")
check("'Place, ST' subject", (rows[0]["location"], rows[0]["state"], rows[0]["yield_bpa"]),
      ("Chatham", "IL", 91))

rows = email("YIELD: Western McDonough Co IL (W IL)",
             "First field went 226 bpa. Better than expected. 247 bpa last year.")
check("Mc-name county in the subject",
      (rows[0]["location"], rows[0]["yield_bpa"], rows[0]["ly_yield"]), ("Western McDonough Co", 226, 247))

rows = email("YIELD: Lee Co GA (SW GA)",
             "Lee Co, GA\nCorn: Whole farm made 211 bu/acre. This was down 9 bushels from last year.")
check("place line + 'Corn:' line are one report",
      [(r["location"], r["crop"], r["yield_bpa"]) for r in rows], [("Lee Co", "Corn", 211)])

rows = email("YIELD: MN soybeans", "*\tPope Co, MN: 30 acres went 54.5 bpa, expected 42. Small beans.")
check("'54.5 bpa, expected 42' -> actual 54.5",
      (rows[0]["yield_bpa"], rows[0]["expected_yield"]), (54.5, 42))

rows = email("YIELD: IL corn",
             "Knox Co IL(western IL) – 1/2 done on corn w 236 avg so far. 110-115 day corn "
             "planted May 2-8. Running 19%")
check("'236 avg'", rows[0]["yield_bpa"], 236)


# --- numbers that look like yields but aren't (each once became a yield) ------
def yields(text):
    return P.extract_yields(text)


check("a planting date is not the low end of a range",
      yields("Story Co, IA: 104 day corn planted 4/12 – 241 bu/acre dry.")[0], [241])
check("a road number is not a yield", yields("Field along Hwy 30- 66 bpa on beans.")[0], [66])
check("'160 A' is acres; '71.4 ave' the yield; '64 LY' last year",
      yields("Boone Co, IA: 160 A 71.4 ave 64 LY")[:2], ([71.4], [64]))
check("'30-50 bu higher YoY' is a change, the yields are in brackets",
      yields("Silage appraisals running 30-50 bu higher YoY (210-240 bpa).")[0], [210, 240])
check("'15-25 bu difference' is a difference",
      yields("Adjusters seeing 15-25 bu difference with fungicide.")[0], [])
check("'silage estimate 218'", yields("Silage estimate 218 this year.")[0], [218])
check("'248 A lot' — an 'A' before a word isn't acres",
      P.extract_yields("Linn Co, IA 248 A lot of fields like it.")[0], [248])
check("a 'last year' that opens its own clause belongs to the next figure",
      yields("Silage estimate 231, fwiw last year silage estimate was 238.")[:2], ([231], [238]))
check("...also after a unit", yields("Made 68 bpa, last year was 73 on that farm.")[0], [68])
check("'better than expected 140 bpa' — 140 is the yield",
      yields("Yields better than expected 140 bpa.")[0], [140])
check("'vs.73 bpa last year' (no space after the stop) is still last year",
      yields("A field made 43 bpa, vs.73 bpa last year.")[:2], ([43], [73]))
check("'expected 220bpa' alone is still the expectation",
      yields("Yield 47 bpa expected 220bpa after hail.")[::2], ([47], [220]))

check("a stated farm average speaks for a report with several fields",
      yields("Polk Co, IA: 80 acres made 182 bpa. Wet spots. Whole farm avg 214 bpa.")[0][0], 214)
check("...but not one that belongs to another place run into the line",
      yields("Polk Co, IA 140 acres @ 220 bpa vs 230 last year. E KS whole farm average "
             "of 190 bu/ac")[0][0], 220)
check("'vs 165 bu/ac target' is the expectation, not a yield",
      yields("Running about 150 bu/ac vs 165 bu/ac target.")[::2], ([150], [165]))

# --- one report, both crops -----------------------------------------------------
mixed = ("Story Co, IA: Corn about 70% out, running 205 bu/ac vs 215 target. Starting on "
         "soybeans. First beans went 61 bu/ac vs 55 LY. Moisture 12%.")
parts = P.split_by_crop(mixed, "Soybeans")
check("yields for both crops -> one part per crop", sorted(parts), ["Corn", "Soybeans"])
check("each part keeps its own figures",
      (P.extract_metrics(parts["Corn"], "Corn")["yield_bpa"],
       P.extract_metrics(parts["Soybeans"], "Soybeans")[("yield_bpa")],
       P.extract_metrics(parts["Soybeans"], "Soybeans")["ly_yield"]), (205, 61, 55))
check("sentences without a crop stay with the crop before them", parts["Soybeans"].endswith("12%."),
      True)
check("a crop named without a yield is not a second report",
      P.split_by_crop("Lee Co, IL: Beans 62 bpa. Corn harvest starts next week.", "Soybeans"), None)
check("'Co.' doesn't end a sentence",
      P._sentences("Polk Co. IA corn 210 bpa. Beans 58 bpa."), ["Polk Co. IA corn 210 bpa.",
                                                              "Beans 58 bpa."])

# --- PDF lines: a report per place, even without a separator --------------------
rows = P.parse_lines(["Corn", "Piatt Co IL Harvest started, 228 bpa dry.",
                      "Macon Co, IL 30 acres did 241 bpa", "Howard Co – 150 bpa",
                      "Alma, MO 16-20% moisture, 205 bpa"], 2026)
check("'Place Co, ST' and 'Town, ST' open reports; a county with no state keeps the last one",
      [(r["location"], r["state"], r["yield_bpa"]) for r in rows],
      [("Piatt Co", "IL", 228), ("Macon Co", "IL", 241), ("Howard Co", "IL", 150),
       ("Alma", "MO", 205)])


# --- the archive matcher -----------------------------------------------------
def stored(**kw):
    base = dict(crop_year=2026, crop="Corn", state="IL", location=None, yield_bpa=None,
                raw_text="", date_reported=None, source_file="pdf", report_source="pdf")
    base.update(kw)
    base["dedup_hash"] = P.dedup_hash(base["crop_year"], base["crop"], base["state"],
                                      base["location"], base["raw_text"])
    return base


archive = pd.DataFrame([
    stored(location="Northern Vermilion County", yield_bpa=262.0,
           raw_text="First field of corn made 262 bpa, vs 271 bpa in 2024, which was a record."),
    stored(location="Knox Co", yield_bpa=228.0,
           raw_text="Knox Co, IL: Running 228-238 bpa, still wet in the +20% area. "
                    "This is at least 10% off last year."),
    stored(state="IA", location="Howard Co",
           raw_text="Howard Co, IA: 2 silage numbers for 215 and 236."),
    stored(location="Sangamon Co", crop="Soybeans", yield_bpa=91.0,
           raw_text="Sangamon Co, IL: 60 acres went 91 bu/acre. Another 60 acres went 83 bu/acre. "
                    "Both records by 8%."),
])


def matched(subject, body, want):
    r = email(subject, body)[0]
    _, h, _ = data.find_match(archive, r)
    got = archive.loc[archive["dedup_hash"] == h, "location"].iloc[0] if h else None
    check(f"match {subject!r}", got, want)


matched("YIELD: Vermilion Co IL corn", "Vermilion Co, IL: 271 bpa on the farm.", None)  # a LY figure there
matched("YIELD: IL corn", "Knox Co IL(western IL) – 1/2 done on corn w 238 avg so far. "
        "110-115 day corn planted May 5-10.", None)                                     # different yield
matched("YIELD: NC IA silage numbers", "Howard Co IA 215", "Howard Co")  # quoted in a no-yield row
matched("YIELD: Chatham, IL soybeans", "60 acres, 91 bu/acre\n60 acres, 83 bu/acre, both records by 8%.",
        "Sangamon Co")                                                   # filed under the county

if failures:
    print(f"FAILED {len(failures)}:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("All parser checks pass (made-up cases).")

local = HERE / "test_parsing_local.py"
if local.exists():
    sys.exit(subprocess.run([sys.executable, str(local)]).returncode)
