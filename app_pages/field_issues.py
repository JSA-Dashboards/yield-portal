"""Field issues — where diseases, weather damage and other problems were noted,
and how often. A reference, not a damage estimate. Internal only."""
import streamlit as st

import analysis
import data
import field_issues as FI
import nass
import places
import report_text as RT

st.title("Field issues")
st.caption("Where diseases, weather damage and other field problems were noted in the "
           "reports, and how often: a memory to look back on. It doesn't estimate what an "
           "issue cost, and it shouldn't move an expectation by itself.")
st.caption(f":material/info: {FI.BLANK_LINE}")

df = data.load_all()
df = df[df["status"] != "excluded"]           # flagged reports' notes still count as notes
if df.empty:
    st.info("No reports yet.")
    st.stop()
seasons_all = sorted(int(y) for y in df["crop_year"].unique())

with st.sidebar:
    st.subheader("Filters")
    crop = st.segmented_control("Crop", ["Both"] + data.CROPS, default="Both",
                                key="fi_crop") or "Both"
    seasons = st.pills("Seasons", seasons_all, selection_mode="multi", default=seasons_all,
                       key="fi_seasons")
    groups = st.pills("Kinds", list(FI.GROUPS), selection_mode="multi", default=list(FI.GROUPS),
                      key="fi_groups")

f = df[df["crop_year"].isin(seasons or [])]
if crop != "Both":
    f = f[f["crop"] == crop]
if f.empty or not groups:
    st.info("Nothing matches these filters.")
    st.stop()

# --- how often ---------------------------------------------------------------------------
counts, totals = FI.frequency(f)
counts = counts[counts.index.map(lambda t: FI.GROUP_OF.get(t, "Other") in groups)]
with st.container(border=True):
    st.markdown("**How often each was noted**")
    st.caption("Reports noting it, out of every report that season (the denominator is in the "
               "column name). A report can note several.")
    if counts.empty:
        st.caption("No issues of these kinds noted in these reports.")
    else:
        tbl = counts.copy()
        cfg = {}
        for y in tbl.columns:
            share = f"{y} %"
            tbl[share] = tbl[y] / totals[y]
            cfg[str(y)] = st.column_config.NumberColumn(f"{y} (of {totals[y]:,})", format="%d")
            cfg[share] = st.column_config.ProgressColumn(f"{y} share", format="percent",
                                                         min_value=0.0, max_value=0.25)
        tbl.columns = [str(c) for c in tbl.columns]
        ordered = [c for y in counts.columns for c in (str(y), f"{y} %")]
        tbl = tbl[ordered].reset_index().rename(columns={"issue": "Issue"})
        tbl.insert(1, "Kind", tbl["Issue"].map(FI.GROUP_OF))
        st.dataframe(tbl, hide_index=True, column_config=cfg)

# --- where ------------------------------------------------------------------------------
long = FI.tags_long(f)
long = long[long["group"].isin(groups)]
if long.empty:
    st.stop()
issues = [t for t in counts.index if t in set(long["issue"])]
with st.container(border=True):
    pick = st.selectbox("Where was it noted?", issues, key="fi_issue")
    hits = long[long["issue"] == pick].sort_values(["crop_year", "state", "location"],
                                                   ascending=[False, True, True])
    st.caption(f"{len(hits):,} reports in {hits[['state', 'location']].drop_duplicates().shape[0]:,} "
               f"places noted {pick.lower()}. Every mention is listed, even a single one. "
               + FI.BLANK_LINE)
    st.dataframe(
        hits.assign(place=hits.apply(lambda r: ", ".join(
            v for v in (r["location"], RT.state_name(r["state"])) if isinstance(v, str) and v),
            axis=1))[["crop_year", "date_reported", "crop", "place", "yield_bpa", "raw_text"]],
        hide_index=True, height=300,
        column_config={
            "crop_year": st.column_config.NumberColumn("Season", format="%d"),
            "date_reported": st.column_config.DateColumn("Reported", format="MMM D"),
            "crop": "Crop", "place": "Place",
            "yield_bpa": st.column_config.NumberColumn("Yield", format="%.1f"),
            "raw_text": st.column_config.TextColumn("Report (as written)", width="large"),
        })

# --- again and again -----------------------------------------------------------------------
rec = FI.recurrence(f, places.place_key)
rec = rec[rec["issue"].map(lambda t: FI.GROUP_OF.get(t, "Other") in groups)]
with st.container(border=True):
    st.markdown("**Noted in more than one season**")
    st.caption("The same issue in the same place in different seasons: the pattern worth a "
               "second look. Still a record of what was written, not a measure of damage.")
    if rec.empty:
        st.caption("No place has the same issue noted in two seasons yet.")
    else:
        st.dataframe(rec[["issue", "where", "state", "seasons", "reports"]], hide_index=True,
                     column_config={"issue": "Issue", "where": "Place", "state": "State",
                                    "seasons": "Seasons", "reports": "Reports"})

# --- yields observed alongside --------------------------------------------------------------
with st.container(border=True):
    st.markdown(f"**Yields observed alongside {pick.lower()}**")
    st.caption(f"Median report against its 5-season NASS average, for reports that noted "
               f"{pick.lower()} and reports in the same state, crop and season that didn't. "
               f"Observed alongside, not caused by: the fields that noted it may differ in "
               f"every other way too. Summarized only where both sides have "
               f"{FI.MIN_SIDE}+ reports.")
    county_raw, state_raw, _ = nass.load(max(seasons_all))
    if county_raw.empty:
        st.caption("Needs the shared NASS cache on Snowflake.")
    else:
        rep = f[f["in_analysis"]]
        rep = analysis.attach(places.match_all(rep, nass.county_index(county_raw),
                                               places.cached_decisions()),
                              nass.county_table(county_raw), nass.state_table(state_raw))
        side = FI.alongside(rep, pick)
        if side.empty:
            st.caption("No report with a yield noted it in these seasons.")
        else:
            side["state"] = side["state"].map(RT.state_name)
            side["read"] = side["enough"].map(lambda ok: "" if ok else
                                              f"too few to compare (fewer than {FI.MIN_SIDE} a side)")
            st.dataframe(
                side.sort_values(["season", "n_noted"], ascending=[False, False])[
                    ["season", "state", "crop", "n_noted", "med_noted", "n_not", "med_not", "read"]],
                hide_index=True,
                column_config={
                    "season": st.column_config.NumberColumn("Season", format="%d"),
                    "state": "State", "crop": "Crop",
                    "n_noted": st.column_config.NumberColumn("Reports noting it"),
                    "med_noted": st.column_config.NumberColumn("their median", format="%.2f×"),
                    "n_not": st.column_config.NumberColumn("Reports not noting it"),
                    "med_not": st.column_config.NumberColumn("their median ", format="%.2f×"),
                    "read": "",
                })
