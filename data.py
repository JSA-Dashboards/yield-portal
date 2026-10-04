"""
Shared data access for the portal pages: one cached load of the whole table,
plus the matching that links an email to the PDF row it reported.
"""
import difflib
import re

import pandas as pd
import streamlit as st

import db
from parse_pdfs import DISEASE_TAGS

# Year colours: the dataviz skill's validated categorical order with JPSI blue in
# slot 1 (validator: all hard checks pass on #ffffff; aqua/yellow sit under 3:1,
# so every multi-year chart ships a table view). Newest archive year takes
# slot 1 and the scale is pinned to the whole archive, so a filter never
# repaints the years that remain. Past 8 years the rest fold to gray.
YEAR_PALETTE = ["#0693e3", "#eb6834", "#1baf7a", "#eda100",
                "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
OTHER_GRAY = "#9aa0a6"
CROPS = ["Corn", "Soybeans"]
IRRIGATION = ["Irrigated", "Non-irrigated", "Mixed"]
DISEASE_OPTIONS = [t for t, _ in DISEASE_TAGS]
NUMERIC = ["yield_bpa", "yield_min", "yield_max", "ly_yield", "expected_yield", "aph"]


@st.cache_data(ttl=600, show_spinner="Loading reports…")
def load_all() -> pd.DataFrame:
    """Every live observation (cached for the pages): superseded rows (replaced
    by others, e.g. a merged PDF line that was split) are dropped. The table is
    small (~1-2k rows), so pages load it once and filter in memory."""
    df = frame()
    return df[df["status"] != "superseded"].reset_index(drop=True)


def frame() -> pd.DataFrame:
    """Every observation, typed, plus derived columns and the review checks
    (checks.run: review_flags, suggestion, status, in_analysis) — uncached, for scripts.
    Superseded rows are included; callers that match or display drop them."""
    import checks                  # here, not at the top: checks imports this module
    rows, decisions = db.fetch_all_and_decisions()
    df = pd.DataFrame(rows, columns=db.COL_NAMES)
    for c in NUMERIC:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["crop_year"] = pd.to_numeric(df["crop_year"], errors="coerce").astype("Int64")
    # Plain dates (or None) in an object column — with every value missing,
    # .dt.date would leave a datetime64 column that rejects later assignments
    # of a date (e.g. the email matcher on a freshly loaded table).
    reported = pd.to_datetime(df["date_reported"], errors="coerce")
    df["date_reported"] = pd.Series(
        [x.date() if pd.notna(x) else None for x in reported], index=df.index, dtype="object")
    for c in ("is_silage", "is_record"):
        df[c] = df[c].map(lambda v: bool(v) if pd.notna(v) else False)
    df["vs_ly"] = df["yield_bpa"] - df["ly_yield"]
    df["vs_aph"] = df["yield_bpa"] - df["aph"]
    return checks.run(df, decisions)


def invalidate():
    """Call after any write so every page sees it on the next rerun."""
    load_all.clear()


def year_scale(years_all):
    """Altair color scale with each year pinned to its colour (see YEAR_PALETTE)."""
    import altair as alt
    ys = sorted((int(y) for y in years_all), reverse=True)
    colors = [YEAR_PALETTE[i] if i < len(YEAR_PALETTE) else OTHER_GRAY
              for i in range(len(ys))]
    return alt.Scale(domain=ys, range=colors)


def has_tag(series: pd.Series, tag: str) -> pd.Series:
    return series.fillna("").str.split(", ").map(lambda tags: tag in tags)


def loc_key(s) -> str:
    """Location reduced for comparison: case, punctuation and the county word
    dropped, so "Morgan CO", "Morgan Co." and "Morgan County" compare equal."""
    s = re.sub(r"\b(co|county|parish)\b", " ", str(s or "").lower())
    return " ".join(re.sub(r"[^a-z ]", " ", s).split())


def _text_key(s) -> str:
    return " ".join(re.sub(r"[^a-z0-9. ]", " ", str(s or "").lower()).split())


def _figures(s) -> set:
    """The telling figures in a report — acres, yields, APH (30+) or anything with
    a decimal (71.5, 35.72). A fingerprint that survives rewording ("80 acres,
    96 bu/acre" vs "80 acres went 96 bu/acre"). Small whole numbers (dates,
    percentages, "May 5-10") are left out: they coincide too easily."""
    nums = re.findall(r"(?<![\d.])\d{2,}(?:\.\d+)?", str(s or ""))
    return {n for n in nums if "." in n or 30 <= float(n) < 1000}


def find_match(df: pd.DataFrame, row: dict):
    """Find the stored row that is the same report as `row` (usually a parsed
    email). -> ("identical" | "match" | None, dedup_hash | None, score).

    Same crop year and state (and crop, when the email names one). Then either
      - the same place ("Western McDonough Co" counts as "McDonough Co") plus the same
        yield, similar wording, two shared figures, or its yield in the stored
        text ("220" in "2 silage numbers for 220 and 250"); or
      - the place is worded differently or missing ("Chatham, IL" was filed under
        "Sangamon Co"): then the same yield, two shared figures and loosely
        similar wording are all required.
    Rows not yet dated are preferred, so a repeat email doesn't re-stamp a row
    that already has its date."""
    if row.get("dedup_hash") in set(df["dedup_hash"]):
        return "identical", row["dedup_hash"], 1.0
    cand = df[(df["crop_year"] == row.get("crop_year")) & (df["state"] == row.get("state"))]
    if row.get("crop") in CROPS:      # an email that names no crop matches either
        cand = cand[cand["crop"] == row["crop"]]
    if cand.empty:
        return None, None, 0.0
    k, t = loc_key(row.get("location")), _text_key(row.get("raw_text"))
    y, figs = row.get("yield_bpa"), _figures(row.get("raw_text"))
    best = (0.0, None)
    for c in cand.itertuples():
        ck = loc_key(c.location)
        if k and ck and min(len(k), len(ck)) >= 4 and (k in ck or ck in k):
            loc_sim = 1.0
        else:
            loc_sim = difflib.SequenceMatcher(None, k, ck).ratio() if k and ck else 0.0
        both_yields = y is not None and pd.notna(c.yield_bpa)
        if both_yields and abs(c.yield_bpa - y) >= 0.51:
            continue        # same place, different yield: a different report
        same_yield = both_yields
        txt_sim = difflib.SequenceMatcher(None, t, _text_key(c.raw_text)).ratio()
        shared = len(figs & _figures(c.raw_text))
        if loc_sim >= 0.8:
            # last clause: a stored row with no yield of its own that quotes this
            # one ("2 silage numbers for 220 and 250")
            ok = (same_yield or txt_sim >= 0.75 or shared >= 2
                  or (y is not None and pd.isna(c.yield_bpa)
                      and f"{y:g}" in _figures(c.raw_text)))
        else:
            ok = same_yield and shared >= 2 and txt_sim >= 0.5
        if not ok:
            continue
        score = (0.4 * loc_sim + 0.4 * txt_sim + 0.1 * min(shared, 4) / 4
                 + (0.2 if same_yield else 0) + (0.1 if pd.isna(c.date_reported) else 0))
        if score > best[0]:
            best = (score, c.dedup_hash)
    return ("match", best[1], best[0]) if best[1] else (None, None, 0.0)


def describe(df: pd.DataFrame, dedup_hash: str) -> str:
    """One-line summary of a stored row, for match previews."""
    r = df.loc[df["dedup_hash"] == dedup_hash]
    if r.empty:
        return ""
    r = r.iloc[0]
    y = f"{r.yield_bpa:g} bpa" if pd.notna(r.yield_bpa) else "no yield"
    dated = f", dated {r.date_reported}" if pd.notna(r.date_reported) else ", undated"
    return f"{r.crop_year} {r.location or '?'}, {r.state}: {y} ({r.source_file or r.report_source}{dated})"
