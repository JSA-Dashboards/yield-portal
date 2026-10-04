"""
Field issues: where diseases, weather damage and other problems were noted in
the reports, and how often. A reference to look back on, not a damage estimate.

A report carries zero or more issue tags (the `disease` column): present or not,
no severity, because reporters don't give it. A missing tag is not evidence the
problem was missing: reporting is voluntary, and every view says so. Yields shown
next to an issue were "observed alongside" it, never "caused by" it, and are only
summarized when both sides have at least MIN_SIDE reports.
"""
import pandas as pd

import parse_pdfs as P

GROUPS = {
    "Disease": ["Tar spot", "Rust", "Gray leaf spot", "Leaf blight", "Crown/root rot",
                "Stalk rot/quality", "Ear rot/mold", "White spot", "Goss's wilt", "Anthracnose",
                "SDS", "White mold", "Frogeye", "Phytophthora", "Disease (general)"],
    "Weather": ["Drought/dry", "Excess water", "Hail", "Wind/lodging", "Heat stress", "Frost"],
    "Pests & field": ["Insects", "Pollination", "Compaction", "Nutrient deficiency"],
}
GROUP_OF = {tag: g for g, tags in GROUPS.items() for tag in tags}
ORDER = [t for t, _ in P.DISEASE_TAGS]
BLANK_LINE = ("Reporting is voluntary, so a county with no mention is not a clean county: "
              "an empty cell means nobody wrote it down.")
MIN_SIDE = 5


def tags_long(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (report, issue noted in it)."""
    t = df.assign(issue=df["disease"].fillna("").str.split(", ")).explode("issue")
    t = t[t["issue"].fillna("").str.len() > 0].copy()
    t["group"] = t["issue"].map(GROUP_OF).fillna("Other")
    return t


def frequency(df: pd.DataFrame) -> tuple:
    """-> (counts: issue x season, totals: reports per season). Each count is the
    number of reports that season noting the issue; totals are the denominators."""
    totals = df.groupby("crop_year").size()
    t = tags_long(df)
    counts = (t.groupby(["issue", "crop_year"]).size().unstack(fill_value=0)
              .reindex(columns=totals.index, fill_value=0))
    counts = counts.loc[sorted(counts.index, key=lambda x: ORDER.index(x) if x in ORDER else 99)]
    return counts, totals


def recurrence(df: pd.DataFrame, place_key) -> pd.DataFrame:
    """Places where the same issue was noted in more than one season: the pattern
    worth a second look (still not a loss estimate)."""
    t = tags_long(df)
    t["place"] = t["location"].map(place_key)
    t = t[t["place"].str.len() > 0]
    g = (t.groupby(["state", "place", "issue"])
         .agg(seasons=("crop_year", lambda s: sorted({int(v) for v in s})),
              where=("location", "first"), reports=("dedup_hash", "size"))
         .reset_index())
    g = g[g["seasons"].map(len) > 1]
    g["seasons"] = g["seasons"].map(lambda s: ", ".join(str(v) for v in s))
    return g.sort_values(["issue", "state", "where"])


def alongside(rep: pd.DataFrame, issue: str) -> pd.DataFrame:
    """Per state, crop and season: reports noting `issue` vs reports not noting it,
    with the median ratio to the 5-season average on each side. Medians only
    when both sides have MIN_SIDE+ reports; otherwise just the counts."""
    noted = rep["disease"].fillna("").str.split(", ").map(lambda tags: issue in tags)
    rows = []
    for (st_, crop, yr), g in rep.assign(noted=noted).groupby(["state", "crop", "crop_year"]):
        w, wo = g[g["noted"]]["r_avg5"].dropna(), g[~g["noted"]]["r_avg5"].dropna()
        if w.empty:
            continue
        enough = len(w) >= MIN_SIDE and len(wo) >= MIN_SIDE
        rows.append({"state": st_, "crop": crop, "season": int(yr), "n_noted": len(w),
                     "med_noted": float(w.median()) if enough else None, "n_not": len(wo),
                     "med_not": float(wo.median()) if enough else None,
                     "enough": enough})
    out = pd.DataFrame(rows, columns=["state", "crop", "season", "n_noted", "med_noted",
                                      "n_not", "med_not", "enough"])
    for c in ("med_noted", "med_not"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out
