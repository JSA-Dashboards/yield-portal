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
