"""
Checks for the Field issues layer: counts with their denominators, recurrence,
and the "observed alongside" gate.

    python tests/test_field_issues.py

The reports below are MADE UP (the repo is public).
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import pandas as pd  # noqa: E402

import field_issues as FI  # noqa: E402
import places  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def rep(year, loc, disease, r=1.1, crop="Corn", state="IL", n=1):
    return [dict(crop_year=year, crop=crop, state=state, location=loc, disease=disease,
                 r_avg5=r, dedup_hash=f"{year}{loc}{i}{disease}", yield_bpa=200.0)
            for i in range(n)]


df = pd.DataFrame(
    rep(2025, "Adams Co", "Tar spot, Hail") + rep(2025, "Brown Co", None)
    + rep(2025, "Clark Co", "Rust") + rep(2026, "Adams Co", "Tar spot")
    + rep(2026, "Ford Co", None) + rep(2026, "Knox Co", "Drought/dry"))

long = FI.tags_long(df)
check("one row per issue noted; a report can note several", len(long), 5)
check("kinds", sorted(set(long["group"])), ["Disease", "Weather"])

counts, totals = FI.frequency(df)
check("denominators: every report that season, noted or not", totals.to_dict(), {2025: 3, 2026: 3})
check("tar spot by season", counts.loc["Tar spot"].to_dict(), {2025: 1, 2026: 1})
check("a season without a mention is 0, not missing", counts.loc["Rust"].to_dict(), {2025: 1, 2026: 0})
check("listed in the parser's order", list(counts.index)[:2], ["Tar spot", "Rust"])

rec = FI.recurrence(df, places.place_key)
check("the same issue, same place, two seasons", rec[["issue", "where", "seasons"]].values.tolist(),
      [["Tar spot", "Adams Co", "2025, 2026"]])

thin = pd.DataFrame(rep(2025, "Adams Co", "Rust", r=1.0, n=4) + rep(2025, "Brown Co", None, r=1.2, n=9))
side = FI.alongside(thin, "Rust")
check("fewer than 5 on a side: counts only, no medians",
      (side.loc[0, "n_noted"], pd.isna(side.loc[0, "med_noted"]), bool(side.loc[0, "enough"])),
      (4, True, False))
wide = pd.DataFrame(rep(2025, "Adams Co", "Rust", r=1.0, n=5) + rep(2025, "Brown Co", None, r=1.2, n=6))
side = FI.alongside(wide, "Rust")
check("5+ on both sides: both medians", (side.loc[0, "med_noted"], side.loc[0, "med_not"]), (1.0, 1.2))
check("the blank-isn't-clean line says so", "not a clean county" in FI.BLANK_LINE, True)

if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("All field-issue checks pass.")
