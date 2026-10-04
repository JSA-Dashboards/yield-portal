"""
Checks for the review layer: which reports get flagged, what fix is suggested,
and how decisions turn flags into a status.

    python tests/test_checks.py

The reports below are MADE UP (the repo is public).
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import pandas as pd  # noqa: E402

import checks  # noqa: E402
import parse_pdfs as P  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def row(text, crop="Corn", state="IL", location="Adams Co", year=2026, **stored):
    """A stored report as the parser would have saved it, then `stored` overrides
    (to fake an old parser's reading or a hand edit)."""
    r = {"crop_year": year, "crop": crop, "state": state, "location": location,
         "raw_text": text, **P.extract_metrics(text, crop)}
    r.update(stored)
    r["dedup_hash"] = P.dedup_hash(year, crop, state, location, text)
    return r


rows = [
    row("Adams Co, IL: 241 bpa dry."),                                        # 0 clean
    row("Brown Co, IL: hail, 38 bpa.", location="Brown Co"),                  # 1 range (low)
    row("Clark Co, IL: 312 bpa, record.", location="Clark Co"),               # 2 range (high)
    row("Story Co, IA: 245 bpa on 3,500 acres.", crop="Soybeans", state="IA",
        location="Story Co"),                                                 # 3 corn?
    row("Polk Co, IA: planted 4/12 – 241 bu/acre.", state="IA", location="Polk Co",
        yield_bpa=12.0, yield_min=12.0),                                      # 4 reread (old parser)
    row("Knox Co, IL: 220 bpa on the hill.", location="Knox Co"),             # 5 duplicate
    row("Knox County, IL: 220 bpa, 20% moisture.", location="Knox County"),   # 6 duplicate
    row("Lee Co, IL: beans 62 bpa.", crop="Soybeans", location="Lee Co"),     # 7 clean
    row("Ford Co, IL: no numbers yet, harvest just starting.", location="Ford Co"),  # 8 no yield
]
df = pd.DataFrame(rows)
out = checks.run(df, {})
flags = out["review_flags"].tolist()
check("clean report", flags[0], [])
check("corn under 50 is out of range", flags[1], ["range"])
check("corn over 300 is out of range", flags[2], ["range"])
check("soybeans over 100: probably corn", flags[3], ["corn?"])
check("...with crop -> Corn suggested", out.loc[3, "suggestion"], {"crop": "Corn"})
check("a figure the parser now reads differently (the stale 12 is also out of range)",
      flags[4], ["range", "reread"])
check("...suggests the fresh reading", out.loc[4, "suggestion"]["yield_bpa"], 241.0)
check("same place, crop, year and yield: both flagged", (flags[5], flags[6]),
      (["duplicate"], ["duplicate"]))
check("'Knox Co' and 'Knox County' are the same place", out.loc[5, "dup_of"], [rows[6]["dedup_hash"]])
check("soybeans in range", flags[7], [])
check("no yield: nothing to flag", flags[8], [])
check("statuses", out["status"].tolist(),
      ["clean", "flagged", "flagged", "flagged", "flagged", "flagged", "flagged", "clean", "clean"])
check("in analysis: clean reports with a yield", out["in_analysis"].tolist(),
      [True, False, False, False, False, False, False, True, False])

h = [r["dedup_hash"] for r in rows]
decided = checks.run(df, {
    h[1]: {"decision": "approved", "flags": "range"},             # hail field is real
    h[2]: {"decision": "excluded", "flags": "range"},             # typo, keep out
    h[4]: {"decision": "approved", "flags": "range"},             # approved for something else
    h[5]: {"decision": "superseded", "flags": None},              # replaced by other rows
})
check("approved for its flag -> counts", decided.loc[1, ["status", "in_analysis"]].tolist(),
      ["approved", True])
check("excluded -> out of averages, still a row", decided.loc[2, ["status", "in_analysis"]].tolist(),
      ["excluded", False])
check("a new flag on an approved report asks again", decided.loc[4, "status"], "flagged")
check("superseded", decided.loc[5, "status"], "superseded")
check("a superseded twin no longer makes a duplicate", decided.loc[6, "review_flags"], [])
check("describe a fix", checks.describe_fix({"crop": "Corn", "yield_bpa": 247.0, "ly_yield": None}),
      "crop → Corn, yield → 247, last year → —")

if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("All review-check tests pass.")
