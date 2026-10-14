"""
Field signal: the yield each state's field reports point to, and how far to trust
it. Feeds the Conditions dashboard's beta "Field check" tab, which sets it beside
the conditions model. Internal: it is built from the reports, so only the admin
portal (whose role can read YIELD_REPORTS) shows it.

A state's number is Reports vs normal's ratio, turned into bushels:
  ratio    median of the season's reports against their county's 5-season NASS
           average (analysis.attach's r_avg5; Ag Trader Talk + JSA, no silage, no
           state-fallback baselines: what Reports vs normal counts)
  premium  how far that ratio has run above the NASS final's own ratio (final /
           the state's 5-season average): the median over every state with 5+
           reports in the OTHER finished seasons. Reports run optimistic.
  implied  ratio / premium x the state's 5-season average, bu/ac

How far to trust it comes only from the finished seasons: each one's implied
yields (premium from the other seasons) against the NASS finals. Those errors (%
of the final, log scale) are fitted by maximum likelihood to
    sigma(n)^2 = base^2 + one^2 / n          n = the state's report count
`base` is what no number of reports removes (the premium moves from season to
season), `one` a single report's scatter. Every run prints the fit and how often
the errors fell inside 1 and 2 sigma, by report count.

Past seasons' reports aren't dated (the annual PDFs), so that evidence is
season-end only. The season in progress also gets a row per week (reports dated
through each Sunday) for the season-so-far line, and a `latest` row with every
report so far (undated ones too).

    python field_signal.py                # dry run: the fit, coverage and this season's rows
    python field_signal.py --apply        # replace FIELD_SIGNAL (Snowflake)
"""
import argparse
import datetime as dt
import math
import os
import pathlib
import sys
import traceback
import uuid

HERE = pathlib.Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env", override=True)
except ModuleNotFoundError:
    pass

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit.logger  # noqa: E402

streamlit.logger.set_log_level("error")     # a script: no "No runtime found" per cached call

import analysis  # noqa: E402
import data  # noqa: E402
import db  # noqa: E402
import nass  # noqa: E402

TABLE = "FIELD_SIGNAL"
PREMIUM_MIN_N = 5          # a state-season counts towards the premium with 5+ reports
MIN_ERRORS = 10            # finished state-seasons needed before the error curve is fitted
COLUMNS = [
    ("crop", "VARCHAR(10)"),
    ("season", "INTEGER"),
    ("state", "VARCHAR(2)"),
    ("kind", "VARCHAR(8)"),             # season (finished) | week | latest (in progress)
    ("as_of", "DATE"),                  # week: that Sunday; latest: the run day; season: NULL
    ("last_report", "DATE"),            # newest report date counted (NULL: none dated)
    ("n_reports", "INTEGER"),
    ("n_undated", "INTEGER"),
    ("ratio", "FLOAT"),                 # median report / its county's 5-season NASS average
    ("premium", "FLOAT"),
    ("premium_from", "VARCHAR(40)"),    # the seasons the premium came from
    ("state_avg5", "FLOAT"),            # NASS state 5-season average, bu/ac
    ("implied", "FLOAT"),               # bu/ac
    ("sigma_pct", "FLOAT"),             # expected error of implied, 1 sd, % of the yield
    ("sigma_base_pct", "FLOAT"),
    ("sigma_one_pct", "FLOAT"),
    ("fit_states", "INTEGER"),          # finished state-seasons the error curve was fitted on
    ("tier", "VARCHAR(12)"),            # analysis.tier(n_reports)
    ("nass_final", "FLOAT"),
    ("usda_current", "FLOAT"),          # the final, else USDA's latest forecast
    ("usda_label", "VARCHAR(20)"),
    ("usda_aug", "FLOAT"),
    ("usda_sep", "FLOAT"),
    ("usda_oct", "FLOAT"),
    ("usda_nov", "FLOAT"),
    ("computed_at", "TIMESTAMP_NTZ"),
]
NAMES = [c for c, _ in COLUMNS]
USDA_MONTHS = ["usda_aug", "usda_sep", "usda_oct", "usda_nov"]


# --- the pieces (pure: tests/test_field_signal.py) ------------------------------------
def counted(rep: pd.DataFrame) -> pd.DataFrame:
    """The reports a state's number rests on, as Reports vs normal counts them: Ag
    Trader Talk + JSA (no seed plots), no corn silage, a ratio against a county's
    5-season average (or a town's state), never a state fallback."""
    keep = (rep["source"].isin(data.HEADLINE_SOURCES)
            & ~(rep["is_silage"] & (rep["crop"] == "Corn"))
            & (rep["avg5_level"] != "state fallback") & rep["r_avg5"].notna())
    out = rep.loc[keep, ["crop", "crop_year", "state", "date_reported", "r_avg5"]].copy()
    out["crop_year"] = out["crop_year"].astype(int)
    out["date_reported"] = [d if isinstance(d, dt.date) else None for d in out["date_reported"]]
    return out.reset_index(drop=True)


def usda_months(state_raw: pd.DataFrame) -> pd.DataFrame:
    """crop, state, year -> USDA's Aug-Nov state forecasts (NaN where none: in 2025
    there was no October report)."""
    f = state_raw[state_raw["period"].isin(nass.FORECAST_MONTH)]
    if f.empty:
        return pd.DataFrame(columns=["crop", "state", "year"] + USDA_MONTHS)
    f = f.assign(col=f["period"].map(lambda p: "usda_" + nass.FORECAST_MONTH[p][0].lower()))
    out = f.pivot_table(index=["crop", "state", "year"], columns="col", values="yield",
                        aggfunc="first").reset_index()
    out.columns.name = None
    for c in USDA_MONTHS:
        if c not in out:
            out[c] = np.nan
    return out[["crop", "state", "year"] + USDA_MONTHS]


def state_seasons(r: pd.DataFrame, state_tbl: pd.DataFrame) -> pd.DataFrame:
    """One row per crop, season and state with reports and a NASS 5-season average:
    n, median ratio, avg5, final (NaN while the season is open), current + label."""
    st_ = state_tbl.set_index(["crop", "state", "year"])
    rows = []
    for (crop, season, state), g in r.groupby(["crop", "crop_year", "state"]):
        key = (crop, state, int(season))
        if key not in st_.index:
            continue
        s = st_.loc[key]
        if not _ok(s["avg5"]):
            continue
        rows.append({"crop": crop, "season": int(season), "state": state, "n": len(g),
                     "ratio": float(g["r_avg5"].median()), "avg5": float(s["avg5"]),
                     "final": float(s["final"]) if _ok(s["final"]) else np.nan,
                     "current": float(s["current"]) if _ok(s["current"]) else np.nan,
                     "label": s["current_label"]})
    return pd.DataFrame(rows, columns=["crop", "season", "state", "n", "ratio", "avg5", "final",
                                       "current", "label"])


def premium(done: pd.DataFrame, leave_out=None):
    """Median of ratio / (final / avg5) over one crop's finished state-seasons with
    PREMIUM_MIN_N+ reports, leaving out season `leave_out` -> (premium, seasons used)
    or (None, [])."""
    d = done[(done["n"] >= PREMIUM_MIN_N) & (done["season"] != leave_out)]
    if d.empty:
        return None, []
    p = d["ratio"] / (d["final"] / d["avg5"])
    return float(p.median()), sorted(int(s) for s in d["season"].unique())


def loso_errors(done: pd.DataFrame) -> pd.DataFrame:
    """One crop's finished state-seasons + premium (from the other seasons), implied
    and err_pct = 100 ln(implied / final); rows with no premium are dropped."""
    out = []
    for season, g in done.groupby("season"):
        p, _ = premium(done, leave_out=season)
        if p is None:
            continue
        g = g.assign(premium=p)
        g["implied"] = g["ratio"] / p * g["avg5"]
        g["err_pct"] = 100 * np.log(g["implied"] / g["final"])
        out.append(g)
    return pd.concat(out, ignore_index=True) if out else done.iloc[:0].assign(
        premium=[], implied=[], err_pct=[])


def fit_sigma(n, err):
    """(base, one) maximising the normal likelihood of `err` (%) given
    sigma^2 = base^2 + one^2 / n, on a 0.1-point grid (base 0-20, one 1-80)."""
    n = np.asarray(n, float)
    e2 = np.asarray(err, float) ** 2
    ones = np.arange(1.0, 80.05, 0.1)
    best = (math.inf, None, None)
    for base in np.arange(0.0, 20.05, 0.1):
        var = base ** 2 + (ones ** 2)[:, None] / n[None, :]
        nll = (np.log(var) + e2[None, :] / var).sum(axis=1)
        j = int(np.argmin(nll))
        if nll[j] < best[0]:
            best = (nll[j], round(float(base), 1), round(float(ones[j]), 1))
    return best[1], best[2]


def sigma_pct(n, base, one):
    return math.sqrt(base ** 2 + one ** 2 / n)


def coverage(n, err, base, one):
    """Share of errors within 1 and within 2 sigma."""
    sig = np.sqrt(base ** 2 + one ** 2 / np.asarray(n, float))
    z = np.abs(np.asarray(err, float)) / sig
    return float((z <= 1).mean()), float((z <= 2).mean())


def sundays(first: dt.date, today: dt.date):
    """The Sunday ending `first`'s week (Mon-Sun), and each one after it through the
    last on or before `today`."""
    d = first + dt.timedelta(days=(6 - first.weekday()) % 7)
    while d <= today:
        yield d
        d += dt.timedelta(days=7)


def build(r: pd.DataFrame, state_tbl: pd.DataFrame, usda: pd.DataFrame, today: dt.date,
          computed_at: str = None):
    """-> (rows for FIELD_SIGNAL, {crop: fit}). `r` is counted(); a season is finished
    for a crop once NASS has a final for one of its states."""
    computed_at = computed_at or dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")
    ss = state_seasons(r, state_tbl)
    usda_k = usda.set_index(["crop", "state", "year"])
    rows, fits = [], {}
    for crop, c in ss.groupby("crop"):
        done = c[c["final"].notna()]
        finished = set(done["season"])
        errs = loso_errors(done)
        fit = None
        if len(errs) >= MIN_ERRORS:
            base, one = fit_sigma(errs["n"], errs["err_pct"])
            cov = coverage(errs["n"], errs["err_pct"], base, one)
            fit = {"base": base, "one": one, "states": len(errs), "within1": cov[0],
                   "within2": cov[1], "errors": errs}
        fits[crop] = fit

        def row(season, state, kind, as_of, g, s):
            n = len(g)
            ratio = float(g["r_avg5"].median())
            p, used = premium(done, leave_out=season if season in finished else None)
            implied = ratio / p * s["avg5"] if p else None
            u = usda_k.loc[(crop, state, season)] if (crop, state, season) in usda_k.index else None
            dates = [d for d in g["date_reported"] if d is not None]
            return {
                "crop": crop, "season": int(season), "state": state, "kind": kind,
                "as_of": as_of, "last_report": max(dates) if dates else None,
                "n_reports": n, "n_undated": n - len(dates), "ratio": ratio,
                "premium": p, "premium_from": ",".join(map(str, used)) or None,
                "state_avg5": s["avg5"], "implied": implied,
                "sigma_pct": sigma_pct(n, fit["base"], fit["one"]) if fit and p else None,
                "sigma_base_pct": fit["base"] if fit else None,
                "sigma_one_pct": fit["one"] if fit else None,
                "fit_states": fit["states"] if fit else None,
                "tier": analysis.tier(n), "nass_final": s["final"],
                "usda_current": s["current"], "usda_label": s["label"],
                **{m: (u[m] if u is not None else None) for m in USDA_MONTHS},
                "computed_at": computed_at}

        cr = r[r["crop"] == crop]
        for s in c.itertuples(index=False):
            s = s._asdict()
            g = cr[(cr["crop_year"] == s["season"]) & (cr["state"] == s["state"])]
            if s["season"] in finished:
                rows.append(row(s["season"], s["state"], "season", None, g, s))
                continue
            dated = g[g["date_reported"].notna()]
            if len(dated):
                for sun in sundays(min(cr.loc[(cr["crop_year"] == s["season"])
                                              & cr["date_reported"].notna(), "date_reported"]),
                                   today):
                    upto = dated[dated["date_reported"] <= sun]
                    if len(upto):
                        rows.append(row(s["season"], s["state"], "week", sun, upto, s))
            now = g[g["date_reported"].isna() | (g["date_reported"] <= today)]
            if len(now):
                rows.append(row(s["season"], s["state"], "latest", today, now, s))
    out = pd.DataFrame(rows, columns=NAMES)
    return out.sort_values(["crop", "season", "state", "kind", "as_of"],
                           na_position="first").reset_index(drop=True), fits


def _ok(v):
    return v is not None and v == v


# --- Snowflake -----------------------------------------------------------------------
def compute(today: dt.date):
    """Every live report through Reports vs normal's pipeline -> build()."""
    import geo
    import places
    df = data.frame()
    df = df[df["in_analysis"]]
    seasons = sorted(int(y) for y in df["crop_year"].dropna().unique())
    county_raw, state_raw, nass_as_of = nass.load(max(seasons))
    if county_raw.empty:
        raise SystemExit("No NASS yields: the cache is read only on Snowflake.")
    state_tbl = nass.state_table(state_raw)
    matched = places.match_all(df, nass.county_index(county_raw), places.cached_decisions())
    rep = analysis.attach(matched, nass.county_table(county_raw), state_tbl, geo.load())
    rows, fits = build(counted(rep), state_tbl, usda_months(state_raw), today)
    return rows, fits, nass_as_of


@db._retry_once
def write(rows: pd.DataFrame):
    """Replace FIELD_SIGNAL with `rows` in one statement (INSERT OVERWRITE from a
    temp table): a reader never sees it half written, and a second run can't double
    it. Recreated when its columns change; the schema's future grants re-apply."""
    conn = db.sf_connect()
    try:
        cur = conn.cursor()
        cols = ", ".join(f"{c} {t}" for c, t in COLUMNS)
        cur.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} ({cols})")
        cur.execute(f"SELECT * FROM {TABLE} LIMIT 0")
        if [d[0].lower() for d in cur.description] != NAMES:
            cur.execute(f"CREATE OR REPLACE TABLE {TABLE} ({cols})")
        stage = f"_fs_stage_{uuid.uuid4().hex[:10]}"
        cur.execute(f"CREATE TEMPORARY TABLE {stage} LIKE {TABLE}")
        marks = ", ".join(["%s"] * len(NAMES))
        cur.executemany(f"INSERT INTO {stage} ({', '.join(NAMES)}) VALUES ({marks})",
                        [tuple(db._clean(v) for v in rec)
                         for rec in rows[NAMES].itertuples(index=False)])
        cur.execute(f"INSERT OVERWRITE INTO {TABLE} SELECT * FROM {stage}")
        cur.execute(f"DROP TABLE IF EXISTS {stage}")
        conn.commit()
    finally:
        conn.close()


def report(rows: pd.DataFrame, fits: dict):
    for crop, fit in fits.items():
        print(f"\n== {crop}")
        if not fit:
            print("   too few finished state-seasons to fit the error curve; no sigma")
            continue
        e = fit["errors"]
        print(f"   error curve from {fit['states']} finished state-seasons: base {fit['base']:.1f}%, "
              f"one report {fit['one']:.1f}% -> sigma n=5 {sigma_pct(5, fit['base'], fit['one']):.1f}%, "
              f"n=20 {sigma_pct(20, fit['base'], fit['one']):.1f}%, "
              f"n=50 {sigma_pct(50, fit['base'], fit['one']):.1f}%")
        print(f"   within 1 sigma {fit['within1']:.0%}, within 2 sigma {fit['within2']:.0%}")
        for lo, hi in ((1, 4), (5, 19), (20, 10 ** 6)):
            b = e[(e["n"] >= lo) & (e["n"] <= hi)]
            if len(b):
                w1, w2 = coverage(b["n"], b["err_pct"], fit["base"], fit["one"])
                print(f"     {lo}-{hi if hi < 10 ** 6 else '':<3} reports: {len(b):2d} state-seasons, "
                      f"RMSE {math.sqrt((b['err_pct'] ** 2).mean()):4.1f}%, within 1/2 sigma "
                      f"{w1:.0%}/{w2:.0%}")
        for season, g in e.groupby("season"):
            print(f"     {season}: premium used {g['premium'].iloc[0]:.3f}, "
                  f"mean error {g['err_pct'].mean():+.1f}% over {len(g)} states")
        cur = rows[(rows["crop"] == crop) & (rows["kind"] == "latest")]
        if len(cur):
            print(f"   {int(cur['season'].iloc[0])} so far (premium {cur['premium'].iloc[0]:.3f} "
                  f"from {cur['premium_from'].iloc[0]}):")
            for x in cur.sort_values("n_reports", ascending=False).itertuples():
                print(f"     {x.state}: {x.n_reports:3d} reports, ratio {x.ratio:.3f} -> "
                      f"{x.implied:6.1f} +-{x.sigma_pct:4.1f}% | USDA {x.usda_label} {x.usda_current:6.1f}")
    print(f"\n{len(rows)} rows: " + ", ".join(f"{k} {v}" for k, v in rows["kind"].value_counts().items()))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help=f"replace {TABLE} with these rows")
    ap.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today(),
                    help="pretend it's this day (YYYY-MM-DD)")
    args = ap.parse_args()
    os.environ["USE_SNOWFLAKE"] = "1"          # the archive of record, and the NASS cache
    assert db.use_snowflake(), db.backend_name()
    print(f"===== field signal {dt.datetime.now():%Y-%m-%d %H:%M:%S}")
    rows, fits, nass_as_of = compute(args.today)
    print(f"NASS cache as of {nass_as_of}")
    report(rows, fits)
    if args.apply:
        write(rows)
        print(f"{TABLE} replaced: {len(rows)} rows")
    else:
        print("Dry run: nothing written (--apply writes).")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
