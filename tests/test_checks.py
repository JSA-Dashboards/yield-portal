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

# --- an email nobody could place ------------------------------------------------------
lost = row("Running about 210 bpa so far.", crop=None, state=None, location=None)
out_lost = checks.run(pd.DataFrame([lost, rows[0]]), {})
check("no crop or state: 'incomplete', out of averages until set",
      (out_lost.loc[0, "review_flags"], out_lost.loc[0, "status"], bool(out_lost.loc[0, "in_analysis"])),
      (["incomplete"], "flagged", False))
check("...and no state: nothing to suggest", out_lost.loc[0, "suggestion"], None)
no_crop = [row("Adams Co IA - 226 bpa, down 15 from last year.", crop=None, state="IA"),
           row("Adams Co IA - 62 bpa, on par with last year.", crop=None, state="IA",
               location="Adams County")]
out_nc = checks.run(pd.DataFrame(no_crop), {})
check("no crop but a corn-sized yield: corn suggested",
      (out_nc.loc[0, "review_flags"], out_nc.loc[0, "suggestion"]), (["incomplete"], {"crop": "Corn"}))
check("no crop and a yield either crop could make: set by hand", out_nc.loc[1, "suggestion"], None)

# --- a report with both crops -------------------------------------------------------
both = row("Story Co, IA: Corn running 205 bu/ac vs 215 target. First beans went 61 bu/ac "
           "vs 55 LY.", crop="Soybeans", state="IA", location="Story Co", notes="from a call")
one = checks.run(pd.DataFrame([both]), {})
check("two crops: flagged 'split' (not 'probably corn')", one.loc[0, "review_flags"], ["split"])
check("...suggesting one report per crop", sorted(one.loc[0, "suggestion"]["split"]),
      ["Corn", "Soybeans"])
check("...described with each part's yield", checks.describe_fix(one.loc[0, "suggestion"]),
      "split into Corn 205 bpa + Soybeans 61 bpa")
kids = checks.split_rows(one.iloc[0].to_dict())
check("the split makes a corn and a soybean report",
      [(k["crop"], k["yield_bpa"], k["ly_yield"]) for k in kids],
      [("Corn", 205.0, None), ("Soybeans", 61.0, 55.0)])
check("...each with its own hash, carrying place and notes",
      (len({k["dedup_hash"] for k in kids} | {both["dedup_hash"]}),
       {k["location"] for k in kids}, {k["notes"] for k in kids}), (3, {"Story Co"}, {"from a call"}))
pre = checks.row_checks(pd.DataFrame([both]))
check("row checks computed ahead give the same result",
      checks.run(pre, {}).loc[0, "suggestion"], one.loc[0, "suggestion"])

# several entries stored as one report (keyed in by hand, or loaded before the parser
# split them): flagged, and the fix makes a report per entry
mixed_text = "Half done, dryland 150-190 bpa while irrigated 215-240 bpa. Tar spot bad."
mixed = row(mixed_text, state="NE", location="Saline Co", yield_bpa=None, yield_min=None,
            yield_max=None, irrigation=None, maturity="112", notes="by phone")
mo = checks.run(pd.DataFrame([mixed]), {})
check("dryland + irrigated: flagged 'entries'", mo.loc[0, "review_flags"], ["entries"])
check("...described with each entry's yield", checks.describe_fix(mo.loc[0, "suggestion"]),
      "split into 2 reports: dryland 150 + irrigated 215 bpa")
halves = checks.entry_rows(mo.iloc[0].to_dict())
check("the fix makes a dryland and an irrigated report",
      [(h["irrigation"], h["yield_bpa"], h["yield_max"]) for h in halves],
      [("Non-irrigated", 150, 190), ("Irrigated", 215, 240)])
check("...each the whole text, with its own hash, the keyed-in maturity and the notes",
      ({h["raw_text"] for h in halves} == {mixed_text},
       len({h["dedup_hash"] for h in halves} | {mixed["dedup_hash"]}),
       {h["maturity"] for h in halves}, {h["notes"] for h in halves}),
      (True, 3, {"112"}, {"by phone"}))
ho = checks.run(pd.DataFrame(halves), {})
check("the halves raise nothing: each is re-read as its own entry",
      ho["review_flags"].tolist(), [[], []])
same = checks.entry_rows(row("Dryland made 200 bpa and irrigated 200 bpa too.", state="NE",
                              location="Saline Co", irrigation=None))
check("entries with the same figure aren't each other's duplicate",
      checks.run(pd.DataFrame(same), {})["review_flags"].tolist(), [[], []])
check("one row a person set to one practice still asks for the other entry",
      checks.run(pd.DataFrame([dict(mixed, irrigation="Irrigated", yield_bpa=215.0,
                                    yield_min=215.0, yield_max=240.0)]), {})
      .loc[0, "review_flags"], ["entries"])

# fields listed one after another, each with its acres and APH
fields_text = ("Saline Co, NE Non-irrigated 35 acres 48.5 BPA APH 57.0, 90 acres 46.0 BPA "
               "APH 55.0 Irrigated 120 acres 68.0 BPA APH 71.0")
fields = row(fields_text, crop="Soybeans", state="NE", location="Saline Co")
fo = checks.run(pd.DataFrame([fields]), {})
check("three fields: 'entries', three reports suggested",
      checks.describe_fix(fo.loc[0, "suggestion"]),
      "split into 3 reports: dryland 48.5 + dryland 46 + irrigated 68 bpa")
kids = checks.entry_rows(fo.iloc[0].to_dict())
check("...each field with its practice, yield and APH",
      [(k["irrigation"], k["yield_bpa"], k["aph"]) for k in kids],
      [("Non-irrigated", 48.5, 57), ("Non-irrigated", 46, 55), ("Irrigated", 68, 71)])
# split earlier by practice only: the dryland half held two fields
old_halves = [dict(kids[2]), dict(fields, irrigation="Non-irrigated", aph=57.0,
                                  dedup_hash=P.dedup_hash(2026, "Soybeans", "NE", "Saline Co",
                                                          fields_text, "Non-irrigated"))]
oo = checks.run(pd.DataFrame(old_halves), {})
check("a report with fewer rows than entries asks again, on each of its rows",
      oo["review_flags"].tolist(), [["entries"], ["entries"]])
check("...and lists its rows, so the fix keeps the one that's an entry already",
      sorted(oo.loc[0, "suggestion"]["siblings"]), sorted(h["dedup_hash"] for h in old_halves))
check("...whose hash the fix makes again", kids[2]["dedup_hash"] in
      {k["dedup_hash"] for k in checks.entry_rows(oo.iloc[1].to_dict())}, True)
check("...and leaves its figures alone when they agree", oo.loc[0, "suggestion"]["updates"], {})
stale = [dict(old_halves[0], aph=57.0), old_halves[1]]      # split with the report's first APH
so = checks.run(pd.DataFrame(stale), {})
check("a kept entry read with another entry's APH takes its own",
      so.loc[0, "suggestion"]["updates"], {kids[2]["dedup_hash"]: {"aph": 71.0}})
twins = checks.entry_rows(row("Saline Co, NE 40 acres made 48 bpa vs 60 bpa last year, 80 acres "
                              "made 48 bpa vs 55 bpa last year.", crop="Soybeans", state="NE",
                              location="Saline Co"))
check("two fields with one yield: each re-read as itself, not the first with that yield",
      ([t["ly_yield"] for t in twins], checks.run(pd.DataFrame(twins), {})["review_flags"].tolist()),
      ([60, 55], [[], []]))
# each split row says which field it is; one yield in two reports is a duplicate only
# when the fields name the same acres (or don't say)
pair = checks.entry_rows(row("Saline Co, NE: 120a 50bpa vs. 55bpa in 2024. 40a 52bpa vs. "
                             "60bpa in 2024.", crop="Soybeans", state="NE", location="Saline Co"))
apart = row("Saline Co, NE: 80 acres went 52. Dry finish.", crop="Soybeans", state="NE",
            location="Saline Co")
po = checks.run(pd.DataFrame(pair + [apart]), {})
check("each split row names its field, in its own words; a whole report has none",
      [f if isinstance(f, str) else None for f in po["field"]],
      ["1 of 2: 120a 50bpa vs. 55bpa in 2024.",
                             "2 of 2: 40a 52bpa vs. 60bpa in 2024.", None])
check("fields of different acres with one yield aren't duplicates",
      po["review_flags"].tolist(), [[], [], []])
same_acres = row("Saline Co, NE: 40 acres went 52 on the hill.", crop="Soybeans", state="NE",
                 location="Saline Co")
check("...the same acres are", checks.run(pd.DataFrame(pair + [same_acres]), {})
      ["review_flags"].tolist(), [[], ["duplicate"], ["duplicate"]])
unsaid = row("Saline Co, NE: beans went 52 on the hill.", crop="Soybeans", state="NE",
             location="Saline Co")
check("...and so are reports that name no acres", checks.run(pd.DataFrame(pair + [unsaid]), {})
      ["review_flags"].tolist(), [[], ["duplicate"], ["duplicate"]])
moved = [dict(old_halves[0], yield_bpa=70.0, aph=57.0), old_halves[1]]
check("...but not when a person changed its yield",
      checks.run(pd.DataFrame(moved), {}).loc[0, "suggestion"]["updates"], {})

if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("All review-check tests pass.")
