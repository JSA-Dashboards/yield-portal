"""Explore — filter the archive and read it by year, state and damage."""
import math

import altair as alt
import pandas as pd
import streamlit as st

import data
import field_issues as FI

VIEW_ONLY = st.session_state.get("view_only", False)
US_STATES_TOPO = "https://cdn.jsdelivr.net/npm/vega-datasets@2.8.0/data/us-10m.json"
STATE_FIPS = {"AL": 1, "AR": 5, "GA": 13, "IL": 17, "IN": 18, "IA": 19, "KS": 20,
              "KY": 21, "LA": 22, "MI": 26, "MN": 27, "MS": 28, "MO": 29, "NE": 31,
              "NC": 37, "ND": 38, "OH": 39, "OK": 40, "SC": 45, "SD": 46, "TN": 47,
              "TX": 48, "WI": 55}
BRAND = "#0693e3"
# Bar spec: rounded data-end; the gap between neighbouring bars comes from band
# padding (a stroke would eat most of a thin grouped bar).
BAR = dict(cornerRadiusEnd=3)
GROUP_GAP = alt.Scale(paddingInner=0.18)      # between a category's year bars
CATEGORY_GAP = alt.Scale(paddingInner=0.3)    # between categories

st.title("Yield reports")
st.caption("County field reports from Ag Trader Talk and JSA, harvest 2023 onward. "
           "Averages are of the reports themselves, not county or state estimates.")

try:
    df = data.load_all()
except Exception as exc:                  # e.g. the database login is refused
    # the view link is public: say what's wrong without the connection details
    st.error("The reports can't be loaded right now. Please try again later."
             if VIEW_ONLY else f"Couldn't read the reports database: {exc}",
             icon=":material/cloud_off:")
    st.stop()
if df.empty:
    st.info("No reports yet. Load them on **Import PDF** or **Add reports**.")
    st.stop()
# Only reports that passed the checks or that a person approved; flagged ones
# wait on Review & edit, excluded ones stay in the archive (Report text).
waiting = int((df["status"] == "flagged").sum())
df = df[df["status"].isin(["clean", "approved"])]
if waiting and not VIEW_ONLY:
    st.caption(f":material/rule: {waiting:,} reports are waiting on **Review & edit** and "
               "aren't counted here until they're cleared.")

years_all = sorted(int(y) for y in df["crop_year"].dropna().unique())
YEAR_COLOR = alt.Color("crop_year:N", title="Year", scale=data.year_scale(years_all))

with st.sidebar:
    st.subheader("Filters")
    crop = st.segmented_control("Crop", data.CROPS, default="Corn", key="ex_crop") or "Corn"
    years = st.pills("Crop year", years_all, selection_mode="multi",
                     default=years_all, key="ex_years")
    states = st.multiselect("States", sorted(df["state"].dropna().unique()),
                            placeholder="All states", key="ex_states")
    irr = st.pills("Irrigation", data.IRRIGATION + ["Not stated"],
                   selection_mode="multi", key="ex_irr")
    tags = st.multiselect("Disease / damage", data.DISEASE_OPTIONS,
                          placeholder="Any", key="ex_tags",
                          help="Shows reports naming any of the selected items.")
    silage = st.toggle("Include silage numbers", value=False, key="ex_silage",
                       disabled=crop != "Corn")
    sources = st.pills("Source", data.SOURCES, selection_mode="multi",
                       default=data.HEADLINE_SOURCES, key="ex_sources",
                       help="Ag Trader Talk's emails and PDFs, JSA's own reports, and seed-company "
                            "plot results customers shared. Plots are off by default: they run "
                            "well above county averages and companies choose what they publish.")
    q = st.text_input("Search", placeholder="County, town, or any word", key="ex_q")

if not years:
    st.warning("Pick at least one crop year in the sidebar.")
    st.stop()
if not sources:
    st.warning("Pick at least one source in the sidebar.")
    st.stop()

f = df[(df["crop"] == crop) & (df["crop_year"].isin(years)) & (df["source"].isin(sources))]
if states:
    f = f[f["state"].isin(states)]
if irr:
    mask = f["irrigation"].isin([i for i in irr if i != "Not stated"])
    if "Not stated" in irr:
        mask |= f["irrigation"].isna()
    f = f[mask]
if tags:
    f = f[pd.concat([data.has_tag(f["disease"], t) for t in tags], axis=1).any(axis=1)]
if crop == "Corn" and not silage:
    f = f[~f["is_silage"]]
if q:
    f = f[f["location"].fillna("").str.contains(q, case=False, regex=False)
          | f["raw_text"].fillna("").str.contains(q, case=False, regex=False)]

if f.empty:
    st.info("No reports match these filters.")
    st.stop()


def _num(v, suffix="", signed=False):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return (f"{v:+.1f}" if signed else f"{v:.1f}") + suffix


def _year_table(long_df, value, fmt):
    """Pivot a (row label x crop_year) frame into one column per year, newest first."""
    wide = long_df.pivot(index=long_df.columns[0], columns="crop_year", values=value)
    wide = wide[sorted(wide.columns, reverse=True)]
    wide.columns = [str(c) for c in wide.columns]
    cfg = {c: st.column_config.NumberColumn(c, format=fmt) for c in wide.columns}
    return wide.reset_index(), cfg


# --- KPI row: the latest selected year, against the one before it --------------
latest = max(years)
prev = max((y for y in years if y < latest), default=None)
h = data.headline(f, latest, prev)


def _pct(v, suffix=""):
    return None if v is None or math.isnan(v) else f"{v:+.1f}%{suffix}"


with st.container(horizontal=True):
    st.metric(f"Reports · {latest}", f"{h['reports']:,}", border=True,
              help="Field reports in the latest selected crop year, after filters.")
    st.metric(f"Avg reported yield · {latest}", _num(h["avg"], " bpa"),
              delta=None if math.isnan(h["avg_change"])
              else f"{h['avg_change']:+.1f} bpa ({h['avg_change_pct']:+.1f}%) vs {prev}",
              border=True,
              help="Simple average of the reported yields. Reports cluster where "
                   "the scouts have contacts, so read it as the tone of the reports.")
    st.metric("Vs same field last year", _num(h["vs_ly"], " bpa", signed=True),
              delta=_pct(h["vs_ly_pct"], " vs LY"), border=True,
              help=f"Average of (yield − last year's yield) over the {h['vs_ly_n']} reports "
                   "that gave both; the % is their gain over last year's bushels.")
    st.metric("Vs APH", _num(h["vs_aph"], " bpa", signed=True),
              delta=_pct(h["vs_aph_pct"], " vs APH"), border=True,
              help=f"Average of (yield − APH) over the {h['vs_aph_n']} reports that gave "
                   "both; the % is their gain over those APH bushels.")
    st.metric("Most-cited damage",
              f"{h['damage'][0]} ({h['damage'][1]})" if h["damage"] else "—",
              border=True, help=f"Most frequent disease / damage tag in {latest}.")

# --- charts -----------------------------------------------------------------
left, right = st.columns(2)
yields = f.dropna(subset=["yield_bpa"]).copy()
yields["crop_year"] = yields["crop_year"].astype(int)

with left, st.container(border=True):
    st.markdown("**Reported yields by crop year**")
    st.caption("Box = middle half of the reports, line = median; "
               "dots = unusual fields (hail, drought, records).")
    box = alt.Chart(yields).mark_boxplot(
        size=34, color=BRAND, outliers={"size": 36, "opacity": 0.55},
    ).encode(
        x=alt.X("crop_year:O", title="Crop year"),
        y=alt.Y("yield_bpa:Q", title="Reported yield (bpa)", scale=alt.Scale(zero=False)),
    ).properties(height=320)
    st.altair_chart(box)

with right, st.container(border=True):
    st.markdown("**By state**")
    by_state = (yields.groupby(["state", "crop_year"])
                .agg(avg=("yield_bpa", "mean"), n=("yield_bpa", "size")).reset_index())
    view = st.segmented_control("State view", ["Map", "Bars", "Table"], default="Map",
                                key="ex_state_view", label_visibility="collapsed") or "Map"
    if view == "Map":
        st.caption(f"Average reported yield, {latest}")
        m = by_state[by_state["crop_year"] == latest].copy()
        m["id"] = m["state"].map(STATE_FIPS)
        topo = alt.topo_feature(US_STATES_TOPO, "states")
        base = alt.Chart(topo).mark_geoshape(fill="#eef2f6", stroke="#ffffff")
        layer = alt.Chart(topo).mark_geoshape(stroke="#ffffff", strokeWidth=1).encode(
            color=alt.Color("avg:Q", title="Avg bpa", scale=alt.Scale(scheme="blues")),
            tooltip=[alt.Tooltip("state:N", title="State"),
                     alt.Tooltip("avg:Q", title="Avg reported bpa", format=".1f"),
                     alt.Tooltip("n:Q", title="Reports")],
        ).transform_lookup(
            lookup="id", from_=alt.LookupData(m, "id", ["state", "avg", "n"])
        ).transform_filter("isValid(datum.avg)")
        st.altair_chart(alt.layer(base, layer).project("albersUsa").properties(height=290))
    elif view == "Bars":
        order = by_state.groupby("state")["n"].sum().sort_values(ascending=False).index.tolist()
        st.altair_chart(alt.Chart(by_state).mark_bar(**BAR).encode(
            y=alt.Y("state:N", sort=order, title=None, scale=CATEGORY_GAP),
            x=alt.X("avg:Q", title="Avg reported yield (bpa)"),
            color=YEAR_COLOR,
            yOffset=alt.YOffset("crop_year:N", sort="descending", scale=GROUP_GAP),
            tooltip=[alt.Tooltip("state:N", title="State"),
                     alt.Tooltip("crop_year:N", title="Year"),
                     alt.Tooltip("avg:Q", title="Avg bpa", format=".1f"),
                     alt.Tooltip("n:Q", title="Reports")],
        ).properties(height=max(300, 44 * len(order))))
    else:
        tbl, cfg = _year_table(by_state[["state", "crop_year", "avg"]], "avg", "%.1f")
        tbl["Reports"] = tbl["state"].map(by_state.groupby("state")["n"].sum())
        st.dataframe(tbl.sort_values("Reports", ascending=False), hide_index=True,
                     column_config={"state": "State", **cfg}, height=320)

with st.container(border=True):
    st.markdown("**Disease & damage mentions**")
    st.caption("Reports naming each, by season. " + FI.BLANK_LINE)
    t = f[["crop_year", "disease"]].dropna()
    if t.empty:
        st.caption("No disease or damage named in these reports.")
    else:
        t = t.assign(tag=t["disease"].str.split(", ")).explode("tag")
        counts = t.groupby(["tag", "crop_year"]).size().reset_index(name="reports")
        counts["crop_year"] = counts["crop_year"].astype(int)
        totals = f.groupby("crop_year").size()
        counts["share"] = counts["reports"] / counts["crop_year"].map(totals)
        order = counts.groupby("tag")["reports"].sum().sort_values(ascending=False).index.tolist()
        dview = st.segmented_control("Damage view", ["Chart", "Table"], default="Chart",
                                     key="ex_dmg_view", label_visibility="collapsed") or "Chart"
        if dview == "Chart":
            st.altair_chart(alt.Chart(counts).mark_bar(**BAR).encode(
                y=alt.Y("tag:N", sort=order, title=None, scale=CATEGORY_GAP),
                x=alt.X("reports:Q", title="Reports naming it"),
                color=YEAR_COLOR,
                yOffset=alt.YOffset("crop_year:N", sort="descending", scale=GROUP_GAP),
                tooltip=[alt.Tooltip("tag:N", title="Disease / damage"),
                         alt.Tooltip("crop_year:N", title="Year"),
                         alt.Tooltip("reports:Q", title="Reports"),
                         alt.Tooltip("share:Q", title="Share of year's reports", format=".0%")],
            ).properties(height=max(220, 44 * len(order))))
        else:
            tbl, cfg = _year_table(counts[["tag", "crop_year", "reports"]], "reports", "%d")
            cfg = {c: st.column_config.NumberColumn(f"{c} (of {totals[int(c)]:,})", format="%d")
                   for c in cfg}                # the denominator in every column
            tbl = tbl.fillna(0)          # no mention that year = 0, not unknown
            st.dataframe(tbl.set_index("tag").loc[order].reset_index(), hide_index=True,
                         column_config={"tag": "Disease / damage", **cfg})

dated = f.dropna(subset=["date_reported", "yield_bpa"])
with st.container(border=True):
    st.markdown("**Through the season**")
    if dated.empty:
        st.caption("Report dates fill in as reports are matched to the report emails "
                   "(or entered on **Add reports**).")
    else:
        d = dated.copy()
        d["crop_year"] = d["crop_year"].astype(int)
        # every year on the same calendar so the panels line up by date
        d["season_day"] = pd.to_datetime(d["date_reported"]).map(lambda x: x.replace(year=2000))
        st.altair_chart(alt.Chart(d).mark_circle(
            size=64, opacity=0.8, color=BRAND, stroke="#ffffff", strokeWidth=1,
        ).encode(
            x=alt.X("season_day:T", title=None, axis=alt.Axis(format="%b %d")),
            y=alt.Y("yield_bpa:Q", title="Reported yield (bpa)", scale=alt.Scale(zero=False)),
            tooltip=[alt.Tooltip("date_reported:T", title="Reported"),
                     alt.Tooltip("location:N"), alt.Tooltip("state:N"),
                     alt.Tooltip("yield_bpa:Q", title="Yield")],
        ).properties(width=230, height=220).facet(
            column=alt.Column("crop_year:O", title=None, sort="descending")))

# --- the reports themselves ---------------------------------------------------
cols = ["crop_year", "date_reported", "state", "location", "yield_bpa", "ly_yield",
        "vs_ly", "aph", "maturity", "irrigation", "disease", "raw_text", "source", "source_file"]
table = f[cols].sort_values(["crop_year", "state", "location"],
                            ascending=[False, True, True], na_position="last")
with st.container(border=True):
    st.markdown(f"**Reports** · {len(table):,}")
    st.dataframe(
        table, hide_index=True, height=440,
        column_config={
            "crop_year": st.column_config.NumberColumn("Year", format="%d"),
            "date_reported": st.column_config.DateColumn("Reported", format="MMM D, YYYY"),
            "state": "State",
            "location": "Location",
            "yield_bpa": st.column_config.NumberColumn(
                "Yield", format="%.1f",
                help="Reported yield (bpa); the first figure when a report gives several."),
            "ly_yield": st.column_config.NumberColumn("LY", format="%.1f",
                                                      help="Same field, last year"),
            "vs_ly": st.column_config.NumberColumn("Vs LY", format="%+.1f"),
            "aph": st.column_config.NumberColumn("APH", format="%.0f"),
            "maturity": st.column_config.TextColumn(
                "Maturity", help="Corn: relative maturity in days. Soybeans: maturity group."),
            "irrigation": "Irrigation",
            "disease": "Disease / damage",
            "raw_text": st.column_config.TextColumn("Report", width="large"),
            "source": "Source",
            "source_file": st.column_config.TextColumn(
                "From", help="The annual PDF a report came from, or a seed plot's company."),
        },
    )
    if not VIEW_ONLY:
        st.download_button("Download CSV", table.to_csv(index=False),
                           file_name=f"yield_reports_{crop.lower()}.csv",
                           mime="text/csv", icon=":material/download:")
