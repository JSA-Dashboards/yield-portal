"""
Reports against NASS: how this season's reports compare with the yields NASS
has for the same places, read the only honest way a thin, optimistic sample
allows: against how the same comparison came out in earlier seasons.

Every report gets three ratios (its yield, the first figure it gives, divided by):
  avg5   the 5-season NASS average before its year     county, else state
  ly     last season's NASS final                      county, else state
  usda   USDA's number for its own season              state: the final, or the
                                                       latest forecast in-season
Reports run optimistic (farmers report good fields), so a ratio near 1.10 is
normal; the signal is this season's ratio against earlier seasons' ratios. A
county baseline takes out which counties happened to report. Where NASS
skipped the county, baselines.py fills in from the county's own trend or the
counties around it before falling back to the state; each baseline says which
(the *_level columns). A place that isn't a county (a town, a region) uses its
state.

Medians, not means, and every report counts once (acres are not used). Each
number carries its report count and a confidence label. Thin counties are pulled
toward their state (shown both raw and pulled). No trend line: too few seasons.
"""
import pandas as pd

import baselines

TIERS = [(10, "Firmer"), (3, "Directional"), (0, "Too few")]
SHRINK_K = 3          # a county's own reports count against 3 reports' worth of its state
COUNTY_LEVELS = ("county", "own trend", "neighbors")     # a baseline for the report's county


def tier(n) -> str:
    """'Firmer' (10+ reports), 'Directional' (3-9), 'Too few' (under 3)."""
    return next(label for floor, label in TIERS if n >= floor)


def _ok(v):
    return v is not None and v == v


def attach(reports: pd.DataFrame, county_tbl: pd.DataFrame, state_tbl: pd.DataFrame,
           geo_frame: pd.DataFrame = None) -> pd.DataFrame:
    """reports (with county_method / county_fips from places.match_all) ->
    + baselines (avg5, ly, usda, usda_label, county_final), where each came from
    (avg5_level / ly_level / final_level: county, own trend, neighbors, state for a
    place that isn't a county, state fallback for a county nothing else covered),
    and the ratios r_avg5, r_ly, r_usda, r_final. `geo_frame` (geo.load()) lets the
    neighbor step run; without it a county NASS skipped falls to its state."""
    cb = baselines.CountyBaselines(county_tbl, baselines.Geo(geo_frame))
    sk = state_tbl.set_index(["crop", "state", "year"])
    rows = []
    for r in reports.itertuples(index=False):
        yr = int(r.crop_year)
        use_county = r.county_method in ("exact", "several", "confirmed") and r.county_fips
        c, src = {"final": None, "ly": None, "avg5": None}, {}
        if use_county:
            for k in c:
                c[k], src[k] = cb.combined(r.crop, r.county_fips, yr, k)
        s = sk.loc[(r.crop, r.state, yr)] if (r.crop, r.state, yr) in sk.index else None
        state_lvl = "state fallback" if use_county else "state"
        base = {}
        for k in ("avg5", "ly"):
            if c[k] is not None:
                base[k] = (c[k], src[k])
            else:
                sv = None if s is None else s[k]
                base[k] = (sv, state_lvl) if _ok(sv) else (None, None)
        rows.append({
            "base_avg5": base["avg5"][0], "avg5_level": base["avg5"][1],
            "base_ly": base["ly"][0], "ly_level": base["ly"][1],
            "base_usda": None if s is None else s["current"],
            "usda_label": None if s is None else s["current_label"],
            "county_final": c["final"], "final_level": src.get("final") if c["final"] else None,
        })
    out = pd.concat([reports.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    for col in ("base_avg5", "base_ly", "base_usda", "county_final"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    y = out["yield_bpa"]
    out["r_avg5"] = y / out["base_avg5"]
    out["r_ly"] = y / out["base_ly"]
    out["r_usda"] = y / out["base_usda"]
    out["r_final"] = y / out["county_final"]
    out["pair_ly_pct"] = (y / out["ly_yield"] - 1) * 100      # like field, reporter's own prior year
    out["pair_aph_pct"] = (y / out["aph"] - 1) * 100
    return out


def summarize(g: pd.DataFrame) -> dict:
    """Medians and counts for one group of reports. A ratio against a state
    fallback (a county nothing nearer covered) stays out of them (spec 01b)."""
    level = {"r_avg5": "avg5_level", "r_ly": "ly_level"}

    def med(col):
        s = g[col]
        if col in level and level[col] in g:
            s = s[g[level[col]] != "state fallback"]
        s = s.dropna()
        return (float(s.median()) if len(s) else None), int(len(s))
    out = {"n": int(g["yield_bpa"].notna().sum())}
    for col in ("r_avg5", "r_ly", "r_usda", "pair_ly_pct", "pair_aph_pct"):
        out[col], out[f"n_{col}"] = med(col)
    out["tier"] = tier(out["n"])
    return out


def by_year(rep: pd.DataFrame, key=None) -> pd.DataFrame:
    """One row per (key, crop_year) with the medians: the history every number is
    read against."""
    keys = ([key] if key else []) + ["crop_year"]
    rows = [{**dict(zip(keys, k if isinstance(k, tuple) else (k,))), **summarize(g)}
            for k, g in rep.groupby(keys)]
    out = pd.DataFrame(rows)
    for col in ("r_avg5", "r_ly", "r_usda", "pair_ly_pct", "pair_aph_pct"):
        if col in out:
            out[col] = pd.to_numeric(out[col], errors="coerce")   # None -> blank, not "None"
    return out


def counties(rep: pd.DataFrame, state_median: float) -> pd.DataFrame:
    """One row per matched county (reports with a county baseline, its own or
    filled in from nearby counties): raw median ratio to the 5-season average,
    and pulled toward the state's median of the reports' ratios (not toward the
    state's NASS yield) by report count: (n x raw + k x state) / (n + k)."""
    import places
    c = rep[rep["avg5_level"].isin(COUNTY_LEVELS)].copy()
    c["county"] = c["county_names"].map(lambda ns: " / ".join(places.pretty(n) for n in ns))
    rows = []
    for name, g in c.groupby("county"):
        n = int(g["r_avg5"].notna().sum())
        raw = float(g["r_avg5"].median()) if n else None
        pulled = (None if raw is None or state_median is None
                  else (n * raw + SHRINK_K * state_median) / (n + SHRINK_K))
        rows.append({"county": name, "n": n, "raw": raw, "pulled": pulled, "tier": tier(n),
                     "avg5": float(g["base_avg5"].median()), "ly": g["base_ly"].median(),
                     "baseline": g["avg5_level"].mode().iloc[0]})
    return pd.DataFrame(rows, columns=["county", "n", "raw", "pulled", "tier", "avg5", "ly",
                                       "baseline"])
