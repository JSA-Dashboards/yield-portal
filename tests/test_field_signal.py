"""
Checks for field_signal: the report premium, the leave-one-season-out errors, the
error-curve fit, the weekly rows and what each row carries.

    python tests/test_field_signal.py

All numbers below are MADE UP (the repo is public).
"""
import datetime as dt
import math
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import field_signal as FS  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def close(label, got, want, tol=1e-9):
    if got is None or abs(got - want) > tol:
        failures.append(f"{label}: got {got!r}, want {want!r} (+-{tol})")


D = dt.date


# --- which reports count -------------------------------------------------------------
rep = pd.DataFrame({
    "crop": ["Corn", "Corn", "Corn", "Corn", "Soybeans"],
    "crop_year": [2026] * 5,
    "state": ["IA"] * 5,
    "date_reported": [D(2026, 9, 1), None, D(2026, 9, 2), D(2026, 9, 3), D(2026, 9, 4)],
    "r_avg5": [1.1, 1.2, 1.3, 1.4, 1.5],
    "source": ["Ag Trader Talk", "JSA", "Seed plot", "Ag Trader Talk", "Ag Trader Talk"],
    "is_silage": [False, False, False, True, True],
    "avg5_level": ["county", "state", "county", "county", "county"],
})
got = FS.counted(rep)
check("seed plots and corn silage are left out; a bean 'silage' flag isn't corn silage",
      got["r_avg5"].tolist(), [1.1, 1.2, 1.5])
rep.loc[0, "avg5_level"] = "state fallback"
check("a state fallback baseline is left out", FS.counted(rep)["r_avg5"].tolist(), [1.2, 1.5])


# --- the premium: other finished seasons, states with 5+ reports ------------------------
done = pd.DataFrame({
    "crop": ["Corn"] * 5,
    "season": [2023, 2023, 2024, 2025, 2025],
    "state": ["IA", "IL", "IA", "IA", "IL"],
    "n": [6, 3, 10, 8, 5],
    "ratio": [1.10, 1.50, 1.32, 1.05, 1.21],
    "avg5": [100.0, 100.0, 100.0, 100.0, 100.0],
    "final": [100.0, 100.0, 110.0, 100.0, 110.0],
})
# premiums: 2023 IA 1.10 (IL has 3 reports: out), 2024 IA 1.20, 2025 IA 1.05 and IL 1.10
p, used = FS.premium(done)
close("premium: median over every finished state-season with 5+ reports", p, 1.10)
check("...and the seasons it came from", used, [2023, 2024, 2025])
p, used = FS.premium(done, leave_out=2025)
close("leaving a season out", p, 1.15)
check("...names the others", used, [2023, 2024])
check("no state-season with 5+ reports: no premium", FS.premium(done[done["n"] < 5]), (None, []))

e = FS.loso_errors(done).set_index(["season", "state"])
close("2025's premium comes from 2023-24 (1.15)", e.loc[(2025, "IA"), "premium"], 1.15)
close("implied = ratio / premium x avg5", e.loc[(2025, "IA"), "implied"], 1.05 / 1.15 * 100)
close("error = 100 ln(implied / final)", e.loc[(2025, "IA"), "err_pct"],
      100 * math.log(1.05 / 1.15 * 100 / 100))
check("a state with under 5 reports still gets an error (only the premium needs 5)",
      (2023, "IL") in e.index, True)
same = done.assign(ratio=done["final"] / done["avg5"] * 1.1)
close("a premium steady across seasons: no error",
      float(FS.loso_errors(same)["err_pct"].abs().max()), 0.0, 1e-9)


# --- the error curve -------------------------------------------------------------------
rng = np.random.default_rng(7)
n = rng.integers(1, 60, 4000)
err = rng.normal(0, np.sqrt(3.0 ** 2 + 25.0 ** 2 / n))
base, one = FS.fit_sigma(n, err)
check("the fit recovers a made-up curve (base 3, one report 25)",
      (abs(base - 3.0) <= 0.6, abs(one - 25.0) <= 1.0), (True, True))
close("sigma(n) = sqrt(base^2 + one^2 / n)", FS.sigma_pct(25, 3.0, 25.0), math.sqrt(9 + 25))
check("coverage counts errors inside 1 and 2 sigma",
      FS.coverage([1, 1, 1, 1], [0.5, 1.5, -2.5, 1.0], 0.0, 1.0), (0.5, 0.75))


# --- Sundays -----------------------------------------------------------------------------
check("weeks end on Sunday, through today",
      list(FS.sundays(D(2026, 9, 2), D(2026, 9, 20))),
      [D(2026, 9, 6), D(2026, 9, 13), D(2026, 9, 20)])
check("a Sunday ends its own week", list(FS.sundays(D(2026, 9, 6), D(2026, 9, 12))),
      [D(2026, 9, 6)])
check("nothing before the first week ends", list(FS.sundays(D(2026, 9, 7), D(2026, 9, 12))), [])


# --- build: season rows, weekly rows, latest ----------------------------------------------
def reports(season, state, ratios, dates=None):
    return pd.DataFrame({"crop": "Corn", "crop_year": season, "state": state, "r_avg5": ratios,
                         "date_reported": dates or [None] * len(ratios)})


r = pd.concat([
    reports(2023, "IA", [1.10] * 6), reports(2024, "IA", [1.20] * 5),
    reports(2025, "IA", [1.15] * 5),
    reports(2026, "IA", [1.30, 1.20, 1.40], [D(2026, 9, 2), D(2026, 9, 9), None]),
], ignore_index=True)
state_tbl = pd.DataFrame({
    "crop": "Corn", "state": "IA", "year": [2023, 2024, 2025, 2026],
    "final": [100.0, 100.0, 100.0, None], "current": [100.0, 100.0, 100.0, 104.0],
    "current_label": ["final", "final", "final", "Sep forecast"],
    "ly": [None] * 4, "avg5": [100.0, 100.0, 100.0, 120.0]})
usda = pd.DataFrame({"crop": ["Corn"], "state": ["IA"], "year": [2026], "usda_aug": [101.0],
                     "usda_sep": [104.0], "usda_oct": [np.nan], "usda_nov": [np.nan]})
rows, fits = FS.build(r, state_tbl, usda, D(2026, 9, 16), computed_at="2026-09-16 06:00:00")
check("a finished season is one row; the open one a row per Sunday and a latest",
      rows[["season", "kind"]].apply(tuple, axis=1).tolist(),
      [(2023, "season"), (2024, "season"), (2025, "season"),
       (2026, "latest"), (2026, "week"), (2026, "week")])
check("columns are the table's", list(rows.columns), FS.NAMES)
wk = rows[rows["kind"] == "week"].set_index("as_of")
check("week rows count the reports dated through that Sunday (undated never)",
      wk["n_reports"].to_dict(), {D(2026, 9, 6): 1, D(2026, 9, 13): 2})
latest = rows[rows["kind"] == "latest"].iloc[0]
check("latest counts everything so far, undated too",
      (latest["n_reports"], latest["n_undated"], latest["as_of"], latest["last_report"]),
      (3, 1, D(2026, 9, 16), D(2026, 9, 9)))
close("latest premium: every finished season (1.10, 1.20, 1.15)", latest["premium"], 1.15)
close("implied = median ratio / premium x the state's 5-season average",
      latest["implied"], 1.30 / 1.15 * 120.0)
check("USDA's months come along",
      (latest["usda_aug"], latest["usda_sep"], latest["usda_current"], latest["usda_label"]),
      (101.0, 104.0, 104.0, "Sep forecast"))
s23 = rows[rows["season"] == 2023].iloc[0]
close("a finished season's premium leaves itself out (1.20, 1.15)", s23["premium"], 1.175)
check("...and says so", s23["premium_from"], "2024,2025")
check("too few finished state-seasons: no error curve, no sigma",
      (fits["Corn"], rows["sigma_pct"].isna().all()), (None, True))
FS.MIN_ERRORS = 3
rows, fits = FS.build(r, state_tbl, usda, D(2026, 9, 16))
check("with enough of them every row gets a sigma from the fit",
      (fits["Corn"]["states"], bool(rows["sigma_pct"].notna().all())), (3, True))
latest = rows[rows["kind"] == "latest"].iloc[0]
close("...for its own report count", latest["sigma_pct"],
      FS.sigma_pct(3, fits["Corn"]["base"], fits["Corn"]["one"]))


# --- USDA's months ---------------------------------------------------------------------------
raw = pd.DataFrame({"crop": "Corn", "state": "IA", "year": 2025,
                    "period": ["YEAR", "YEAR - AUG FORECAST", "YEAR - SEP FORECAST",
                               "YEAR - NOV FORECAST"],
                    "yield": [200.0, 205.0, 204.0, 201.0], "loaded": ""})
m = FS.usda_months(raw).iloc[0]
check("one column per forecast month; a month with no report is blank",
      (m["usda_aug"], m["usda_sep"], m["usda_oct"] != m["usda_oct"], m["usda_nov"]),
      (205.0, 204.0, True, 201.0))

if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("All field signal checks pass.")
