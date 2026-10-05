"""Reports vs normal — this season's reports against NASS yields for the same
places, read against the same comparison in earlier seasons. Internal only."""
import pandas as pd
import streamlit as st

import analysis
import data
import geo
import nass
import places
import report_text as RT

st.title("Reports vs normal")
st.caption("How the reports compare with NASS yields for the same places: the 5-season "
           "average, last season, and USDA's number for the season. Reports run optimistic "
           "(a ratio of 1.1 to 1.2 is usual), so read each number against the same ratio in "
           "earlier seasons, not against 1.0.")


df = data.load_all()
reports = df[df["in_analysis"]]
if reports.empty:
    st.info("No reports to compare yet.")
    st.stop()
seasons = sorted(int(y) for y in reports["crop_year"].unique())
county_raw, state_raw, as_of = nass.load(max(seasons))
if county_raw.empty:
    st.info("NASS yields come from the shared cache on Snowflake; this copy of the portal "
            "isn't connected to it.", icon=":material/cloud_off:")
    st.stop()

with st.sidebar:
    st.subheader("Filters")
    crop = st.segmented_control("Crop", data.CROPS, default="Corn", key="nv_crop") or "Corn"
    season = st.segmented_control("Season", seasons[::-1], default=seasons[-1],
                                  key="nv_season") or seasons[-1]
    silage = st.toggle("Include silage numbers", value=False, key="nv_silage",
                       disabled=crop != "Corn")

state_tbl = nass.state_table(state_raw)
matched = places.match_all(reports, nass.county_index(county_raw), places.cached_decisions())
rep = analysis.attach(matched, nass.county_table(county_raw), state_tbl, geo.load())
rep = rep[rep["crop"] == crop]
if crop == "Corn" and not silage:
    rep = rep[~rep["is_silage"]]
cur = rep[rep["crop_year"] == season]
if cur.empty:
    st.info(f"No {crop.lower()} reports for {season} yet.")
    st.stop()

usda_label = cur["usda_label"].dropna().mode()
usda_label = usda_label.iloc[0] if len(usda_label) else "final"
hist = analysis.by_year(rep).set_index("crop_year")
now = hist.loc[season]
before = hist[hist.index < season]


def _x(v):
    return "—" if v is None or v != v else f"{v:.2f}×"


def _band(col):
    vals = before[before["n"] >= 3][col].dropna()
    return "no earlier season" if vals.empty else (
        f"{vals.min():.2f}×–{vals.max():.2f}×" if len(vals) > 1 else f"{vals.iloc[0]:.2f}×")


def _delta(col):
    if before.empty or now[col] is None or before[col].dropna().empty:
        return None
    last = before[col].dropna().iloc[-1]
    return f"{now[col] - last:+.2f} vs {int(before[col].dropna().index[-1])}"


# --- the season in four numbers -------------------------------------------------------
with st.container(horizontal=True):
    st.metric("vs 5-season average", _x(now["r_avg5"]), delta=_delta("r_avg5"), delta_color="off",
              border=True, help=f"Median of each report's yield ÷ its county's NASS average for "
                                f"the 5 seasons before. Where NASS skipped the county, the counties "
                                f"around it stand in; the state's where the place isn't a county. "
                                f"{now['n_r_avg5']} reports · {analysis.tier(now['n_r_avg5'])}. "
                                f"Earlier seasons: {_band('r_avg5')}.")
    st.metric("vs last season", _x(now["r_ly"]), delta=_delta("r_ly"), delta_color="off",
              border=True, help=f"Yield ÷ last season's NASS final for the same county: its own "
                                f"trend for a one-year gap, else the counties around it, else the "
                                f"state. {now['n_r_ly']} reports · {analysis.tier(now['n_r_ly'])}. "
                                f"Earlier seasons: {_band('r_ly')}.")
    st.metric(f"vs USDA ({usda_label})", _x(now["r_usda"]), delta=_delta("r_usda"),
              delta_color="off", border=True,
              help=f"Yield ÷ USDA's state yield for {season} ({usda_label}). Earlier seasons are "
                   f"against the final: {_band('r_usda')}. {now['n_r_usda']} reports · "
                   f"{analysis.tier(now['n_r_usda'])}.")
    pair = now["pair_ly_pct"]
    st.metric("Same field vs last year", "—" if pair is None else f"{pair:+.0f}%", border=True,
              help=f"Median change on reports that give the same field's yield last year — no "
                   f"NASS involved. {now['n_pair_ly_pct']} reports · "
                   f"{analysis.tier(now['n_pair_ly_pct'])}.")

# --- every season, so this one can be read against the others ---------------------------
with st.container(border=True):
    st.markdown("**Season by season** · all states")
    seasons_tbl = hist.reset_index().sort_values("crop_year", ascending=False)
    seasons_tbl["usda_vs"] = seasons_tbl["crop_year"].map(
        lambda y: usda_label if y == season else "final")
    st.dataframe(
        seasons_tbl[["crop_year", "n", "tier", "r_avg5", "r_ly", "r_usda", "usda_vs",
                     "pair_ly_pct", "n_pair_ly_pct"]],
        hide_index=True,
        column_config={
            "crop_year": st.column_config.NumberColumn("Season", format="%d"),
            "n": st.column_config.NumberColumn("Reports"),
            "tier": "Confidence",
            "r_avg5": st.column_config.NumberColumn("vs 5-season avg", format="%.2f×"),
            "r_ly": st.column_config.NumberColumn("vs last season", format="%.2f×"),
            "r_usda": st.column_config.NumberColumn("vs USDA", format="%.2f×"),
            "usda_vs": "USDA number",
            "pair_ly_pct": st.column_config.NumberColumn("Same field vs LY", format="%+.0f%%"),
            "n_pair_ly_pct": st.column_config.NumberColumn("Same-field reports"),
        })
    st.caption("A season's ratios move with which fields happened to report, so a change of a "
               "few hundredths is noise. Look for this season sitting outside the earlier ones.")

# --- by state ------------------------------------------------------------------------------
sh = analysis.by_year(rep, "state")
this = sh[sh["crop_year"] == season].set_index("state")
usual = (sh[(sh["crop_year"] < season) & (sh["n"] >= 3)]
         .groupby("state")[["r_avg5", "r_ly", "r_usda"]].median())
states = this.join(usual, rsuffix="_usual").reset_index()
states["name"] = states["state"].map(RT.state_name)
states = states.sort_values("n", ascending=False)
with st.container(border=True):
    st.markdown(f"**By state** · {season}")
    st.dataframe(
        states[["name", "n", "tier", "r_avg5", "r_avg5_usual", "r_ly", "r_ly_usual",
                "r_usda", "r_usda_usual"]],
        hide_index=True,
        column_config={
            "name": "State",
            "n": st.column_config.NumberColumn("Reports"),
            "tier": "Confidence",
            "r_avg5": st.column_config.NumberColumn("vs 5-season avg", format="%.2f×"),
            "r_avg5_usual": st.column_config.NumberColumn("usual", format="%.2f×",
                                                          help="Median of earlier seasons with 3+ reports"),
            "r_ly": st.column_config.NumberColumn("vs last season", format="%.2f×"),
            "r_ly_usual": st.column_config.NumberColumn("usual ", format="%.2f×"),
            "r_usda": st.column_config.NumberColumn(f"vs USDA ({usda_label})", format="%.2f×"),
            "r_usda_usual": st.column_config.NumberColumn("usual (vs final)", format="%.2f×"),
        })
    quiet = sorted(set(sh.loc[sh["crop_year"] < season, "state"]) - set(this.index))
    if quiet:
        st.caption("No reports yet this season from: "
                   + ", ".join(RT.state_name(s) for s in quiet) + ".")

# --- one state's counties ---------------------------------------------------------------------
with st.container(border=True):
    pick = st.selectbox("Counties in", states["state"].tolist(), format_func=RT.state_name,
                        key=f"nv_state_{crop}_{season}")
    srows = cur[cur["state"] == pick]
    state_median = this.loc[pick, "r_avg5"] if pick in this.index else None
    ct = analysis.counties(srows, state_median)
    st.caption(f"Each county's median ratio to its own 5-season NASS average (from the counties "
               f"around it where NASS skipped the county). A single report says more about that "
               f"field than the county, so the **pulled** column moves thin counties toward "
               f"{RT.state_name(pick)}'s median ({_x(state_median)}). Reports naming a town or "
               f"region use the state's numbers and aren't listed here.")
    st.dataframe(
        ct.sort_values(["n", "county"], ascending=[False, True]), hide_index=True,
        column_config={
            "county": "County",
            "n": st.column_config.NumberColumn("Reports"),
            "raw": st.column_config.NumberColumn("vs 5-season avg (raw)", format="%.2f×"),
            "pulled": st.column_config.NumberColumn("pulled toward state", format="%.2f×"),
            "tier": "Confidence",
            "avg5": st.column_config.NumberColumn("County 5-season avg", format="%.1f"),
            "ly": st.column_config.NumberColumn("County last season", format="%.1f"),
            "baseline": st.column_config.TextColumn(
                "Baseline from", help="county: its own NASS figures. neighbors: NASS skipped "
                                      "the county, so the counties around it, weighted by "
                                      "closeness."),
        })

# --- the reports underneath ----------------------------------------------------------------
with st.expander(f"The {len(srows)} {RT.state_name(pick)} reports behind these numbers"):
    ev = srows.assign(
        county=srows["county_names"].map(lambda ns: " / ".join(places.pretty(n) for n in ns)),
    )[["date_reported", "location", "county", "yield_bpa", "base_avg5", "avg5_level", "r_avg5",
       "base_ly", "ly_level", "r_ly", "base_usda", "r_usda", "raw_text"]]
    st.dataframe(ev, hide_index=True, column_config={
        "date_reported": st.column_config.DateColumn("Reported", format="MMM D"),
        "location": "Place", "county": "NASS county",
        "yield_bpa": st.column_config.NumberColumn("Yield", format="%.1f"),
        "base_avg5": st.column_config.NumberColumn("5-season avg", format="%.1f"),
        "avg5_level": "from",
        "r_avg5": st.column_config.NumberColumn("ratio", format="%.2f×"),
        "base_ly": st.column_config.NumberColumn("Last season", format="%.1f"),
        "ly_level": "from ",
        "r_ly": st.column_config.NumberColumn("ratio ", format="%.2f×"),
        "base_usda": st.column_config.NumberColumn("USDA", format="%.1f"),
        "r_usda": st.column_config.NumberColumn("ratio  ", format="%.2f×"),
        "raw_text": st.column_config.TextColumn("Report", width="large"),
    })

pending = int((matched["county_method"] == "suggested").sum())
src = cur["avg5_level"].value_counts()
src_note = " · ".join(f"{int(src[k])} {label}" for k, label in
                      (("county", "county"), ("neighbors", "nearby counties"),
                       ("own trend", "county trend"), ("state", "state (town or region)"),
                       ("state fallback", "state fallback, left out of the medians"))
                      if src.get(k))
st.caption(f"{season} baselines (5-season average): {src_note}. "
           f"NASS data as of {pd.Timestamp(as_of).strftime('%b %d, %Y') if as_of else '—'}, "
           f"from the shared NASS cache."
           + (f" {pending} reports name a place that may be a county; confirm them on "
              f"**Review & edit → Places** and they'll use the county's numbers." if pending else ""))
