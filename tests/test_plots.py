"""
Checks for seed plots read against their own history (plots.py).

    python tests/test_plots.py

All plots and yields are MADE UP.
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import pandas as pd  # noqa: E402

import plots  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def plot(company, place, year, bpa, crop="Corn", state="ZZ"):
    return dict(crop=crop, crop_year=year, source_file=company, state=state, location=place,
                yield_bpa=bpa)


# a plot gaining 2 bpa a season, 2018-2025, then 2026 comes in 5% over its line
rows = [plot("Acme", "Logan Co", y, 220 + 2 * (y - 2018)) for y in range(2018, 2026)]
rows.append(plot("Acme", "Logan County", 2026, round((220 + 2 * 8) * 1.05, 1)))
# a plot with only 3 earlier seasons, and another company's plot at the same place
rows += [plot("Acme", "Polk Co", y, 210.0) for y in (2023, 2024, 2025, 2026)]
rows.append(plot("Other", "Logan Co", 2026, 250.0))
cmp = plots.compare(pd.DataFrame(rows))

now = cmp[(cmp["crop_year"] == 2026) & (cmp["source_file"] == "Acme")
          & (cmp["location"].str.startswith("Logan"))].iloc[0]
check("own trend from the 8 earlier seasons ('Logan Co' and 'Logan County' are one plot)",
      (now["history"], round(now["own_trend"], 6), now["basis"]), (8, 236.0, "own history"))
check("this season against its own trend", round(now["vs_trend_pct"], 6), 5.0)
check("against the same plot last season", round(now["vs_ly_pct"], 2), round((247.8 / 234 - 1) * 100, 2))
thin = cmp[(cmp["crop_year"] == 2026) & (cmp["location"] == "Polk Co")].iloc[0]
check("3 earlier seasons: too little history for a trend, still vs last season",
      (thin["basis"], thin["own_trend"] != thin["own_trend"], round(thin["vs_ly_pct"], 6)),
      ("too little history", True, 0.0))
other = cmp[cmp["source_file"] == "Other"].iloc[0]
check("another company's plot at the same place is its own series", other["history"], 0)

season = plots.by_season(cmp).set_index("crop_year").loc[2026]
check("season: one plot with a trend, its median", (season["plots"], season["with_trend"],
                                                    round(season["vs_trend_pct"], 6)), (3, 1, 5.0))
check("two seasons or fewer: no line", plots.trend_at([(2025, 200.0)], 2026), None)
check("empty", len(plots.compare(pd.DataFrame(columns=["crop", "crop_year", "source_file", "state",
                                                       "location", "yield_bpa"]))), 0)

if failures:
    print(f"FAILED {len(failures)}:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("All plot checks pass.")
