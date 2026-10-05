"""
Seed plots read against their own history. A company's plot sits at the same
place year after year, so it is its own best baseline: each plot season is set
against the straight-line trend of that plot's earlier seasons, and against the
same plot last season. NASS (the county and neighbor baselines the field reports
use) is only the fallback for a plot with too little history.

A plot is a series: crop, company (source_file), state and place
(data.loc_key), built from the seed-plot rows (report_source 'plot', usually
entered as a whole history on Add reports).
"""
import pandas as pd

import analysis
import data

MIN_HISTORY = 5        # earlier seasons a plot needs before its own trend is used


def trend_at(points, year):
    """Straight-line least squares through (season, yield) points, at `year`;
    None with fewer than 2 distinct seasons."""
    pts = [(y, v) for y, v in points if v is not None and v == v]
    if len({y for y, _ in pts}) < 2:
        return None
    n = len(pts)
    mx, my = sum(y for y, _ in pts) / n, sum(v for _, v in pts) / n
    sxx = sum((y - mx) ** 2 for y, _ in pts)
    slope = sum((y - mx) * (v - my) for y, v in pts) / sxx
    return my + slope * (year - mx)


def compare(plots: pd.DataFrame) -> pd.DataFrame:
    """Seed-plot rows -> one row each, plus: plot (a readable name), history
    (earlier seasons on record), own_trend (what its earlier seasons' line
    expects this season), vs_trend_pct, plot_ly (the same plot last season),
    vs_ly_pct, and basis: 'own history' or 'too little history'."""
    cols = ["plot", "history", "own_trend", "vs_trend_pct", "plot_ly", "vs_ly_pct", "basis"]
    if plots.empty:
        return plots.assign(**{c: pd.Series(dtype="object") for c in cols})
    p = plots.copy()
    p["_key"] = list(zip(p["crop"], p["source_file"].fillna(""), p["state"],
                         p["location"].map(data.loc_key)))
    extra = {}
    for _key, g in p.groupby("_key"):
        seasons = {}
        for r in g.itertuples():
            if r.yield_bpa == r.yield_bpa and r.yield_bpa is not None:
                seasons[int(r.crop_year)] = float(r.yield_bpa)
        for r in g.itertuples():
            y = int(r.crop_year)
            prior = sorted((yy, v) for yy, v in seasons.items() if yy < y)
            trend = trend_at(prior, y) if len(prior) >= MIN_HISTORY else None
            ly = seasons.get(y - 1)
            yv = r.yield_bpa if r.yield_bpa == r.yield_bpa else None
            extra[r.Index] = {
                "plot": f"{r.source_file or 'Seed'} · {r.location or '?'}, {r.state}",
                "history": len(prior), "own_trend": trend,
                "vs_trend_pct": (yv / trend - 1) * 100 if yv and trend else None,
                "plot_ly": ly, "vs_ly_pct": (yv / ly - 1) * 100 if yv and ly else None,
                "basis": "own history" if trend is not None else "too little history"}
    out = p.drop(columns="_key").join(pd.DataFrame.from_dict(extra, orient="index"))
    for c in ("own_trend", "vs_trend_pct", "plot_ly", "vs_ly_pct"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def by_season(cmp: pd.DataFrame) -> pd.DataFrame:
    """Per season: plots with their own trend, the median against it, the median
    against the same plot last season, and a confidence label by plot count."""
    rows = []
    for season, g in cmp.groupby("crop_year"):
        t, ly = g["vs_trend_pct"].dropna(), g["vs_ly_pct"].dropna()
        rows.append({"crop_year": int(season), "plots": int(len(g)), "with_trend": int(len(t)),
                     "vs_trend_pct": float(t.median()) if len(t) else None,
                     "vs_ly_pct": float(ly.median()) if len(ly) else None,
                     "tier": analysis.tier(len(t))})
    out = pd.DataFrame(rows, columns=["crop_year", "plots", "with_trend", "vs_trend_pct",
                                      "vs_ly_pct", "tier"])
    for c in ("vs_trend_pct", "vs_ly_pct"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out
