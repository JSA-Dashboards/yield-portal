"""Strip trials — the Iowa Soybean Association's on-farm strip trials, each field's
yield beside its county's NASS yield."""
import datetime

import altair as alt
import pandas as pd
import streamlit as st

import isa_trials
import nass

VIEW_ONLY = st.session_state.get("view_only", False)
FIELD_INK = "#0693e3"
COUNTY_INK = "#1baf7a"
STATE_INK = "#9aa0a6"
SERIES = ["Strip-trial fields", "Their counties (NASS)", "Iowa (NASS)"]
SCALE = alt.Scale(domain=SERIES, range=[FIELD_INK, COUNTY_INK, STATE_INK])

st.title("Strip trials")
st.caption("The Iowa Soybean Association's replicated on-farm strip trials, 2005 onward: "
           "farmers comparing two or more practices across a whole field, so each trial "
           "is one real field's yield, read against its county's NASS yield.")

if VIEW_ONLY:                       # belt and braces: not registered on the view link
    st.stop()

try:
    trials = isa_trials.load()
except Exception as exc:                  # e.g. the database login is refused
    st.error(f"Couldn't read the strip-trial table: {exc}", icon=":material/cloud_off:")
    st.stop()
if trials.empty:
    st.info("The strip-trial table isn't loaded in this database yet.",
            icon=":material/database:")
    st.code("python load_isa.py fetch\npython load_isa.py load --target snowflake",
            language="bash")
    st.caption("`fetch` downloads ISA's trial list and each report's text, a few hours "
               "the first time, so run it on the Droplet.")
    st.stop()

county_raw, state_raw, _ = nass.load(datetime.date.today().year)
trials = isa_trials.with_nass(trials, county_raw, nass.state_table(state_raw))
HAVE_NASS = not county_raw.empty


@st.fragment
def strip_trials():
    with st.container(horizontal=True):
        crop = st.segmented_control("Crop", ["Corn", "Soybeans"], default="Corn",
                                    key="isa_crop") or "Corn"
    span = (int(trials.year.min()), int(trials.year.max()))
    lo, hi = st.slider("Seasons", span[0], span[1], span, key="isa_years")
    q = st.text_input("Search trials", placeholder="County, district, trial type or product",
                      key="isa_q", label_visibility="collapsed")
    f = trials[(trials.crop == crop) & (trials.year >= lo) & (trials.year <= hi)]
    if q:
        # a blank district comes back NULL, and one None would make the mask NaN
        hay = f[["county", "district", "trial_type", "trial_detail"]].fillna("").agg(" ".join, axis=1)
        f = f[hay.str.contains(q, case=False, regex=False)]
    read = f[f.status.isin(isa_trials.READ)]
    if read.empty:
        st.info("No trials with read yields match these filters.")
        return

    with st.container(horizontal=True):
        st.metric("Trials read", f"{len(read):,} of {len(f):,}", border=True,
                  help="Trials whose yields were read from ISA's report and agree with the "
                       "response ISA lists for the trial. The rest are in the table below, "
                       "uncharted.")
        st.metric("Counties", f"{read.county.nunique()}", border=True,
                  help="Iowa counties with a read trial in the filter.")
        paired = read.vs_county.dropna()
        st.metric("Field over its county", f"{paired.median():+.1%}" if len(paired) else "—",
                  border=True,
                  help="Each trial's yield over its county's NASS yield that season, the "
                       "median across trials (%d paired). A county NASS didn't publish that "
                       "season has no figure." % len(paired))
        st.metric("Field over Iowa", f"{read.vs_state.median():+.1%}"
                  if read.vs_state.notna().any() else "—", border=True,
                  help="Each trial's yield over Iowa's NASS yield that season, the median "
                       "across trials.")

    season = isa_trials.by_season(read)
    with st.container(border=True):
        head, toggle = st.columns([3, 1], vertical_alignment="center")
        head.markdown(f"**{crop}: strip-trial fields against NASS, by season**")
        view = toggle.segmented_control("View", ["Chart", "Table"], default="Chart",
                                        key="isa_view", label_visibility="collapsed") or "Chart"
        if view == "Chart":
            long = pd.concat([
                season[["year", "field", "trials"]].rename(columns={"field": "value"})
                .assign(series=SERIES[0]),
                season[["year", "county", "trials"]].rename(columns={"county": "value"})
                .assign(series=SERIES[1]),
                season[["year", "state", "trials"]].rename(columns={"state": "value"})
                .assign(series=SERIES[2]),
            ]).dropna(subset=["value"])
            long["year"] = long.year.astype(int)
            hover = alt.selection_point(on="pointerover", nearest=True, fields=["year"],
                                        empty=False)
            base = alt.Chart(long).encode(
                x=alt.X("year:Q", title=None, axis=alt.Axis(format="d"),
                        scale=alt.Scale(nice=False)),
                y=alt.Y("value:Q", title=f"{crop} yield (bu/acre)", scale=alt.Scale(zero=False)),
                # a legend, not end labels: the two NASS lines often finish a bushel apart
                color=alt.Color("series:N", title=None, scale=SCALE,
                                legend=alt.Legend(orient="top", symbolType="stroke")),
            )
            solid = base.transform_filter(alt.datum.series != SERIES[2]).mark_line(
                strokeWidth=2, point=alt.OverlayMarkDef(size=30))
            dashed = base.transform_filter(alt.datum.series == SERIES[2]).mark_line(
                strokeWidth=1.5, strokeDash=[4, 3])
            marks = base.mark_circle(size=90, opacity=0).add_params(hover)
            tips = base.mark_circle(size=110, stroke="#ffffff", strokeWidth=2).encode(
                opacity=alt.condition(hover, alt.value(1), alt.value(0)),
                tooltip=[alt.Tooltip("series:N", title=None),
                         alt.Tooltip("year:Q", title="Season", format="d"),
                         alt.Tooltip("value:Q", title="Yield", format=".1f"),
                         alt.Tooltip("trials:Q", title="Trials read")])
            st.altair_chart(alt.layer(solid, dashed, marks, tips).properties(height=360))
            st.caption("Mean yield of the season's read trials, and the mean NASS yield of "
                       "the counties they sat in (where NASS published the county). The "
                       "trials move from county to county every year, so read the gap "
                       "between the two lines, not the level of either.")
        else:
            shown = season.drop(columns="crop").sort_values("year", ascending=False)
            shown[["over_county", "over_state"]] *= 100
            st.dataframe(
                shown,
                hide_index=True,
                column_config={
                    "year": st.column_config.NumberColumn("Season", format="%d"),
                    "trials": st.column_config.NumberColumn("Trials read", format="%d"),
                    "field": st.column_config.NumberColumn("Fields", format="%.1f",
                                                           help="Mean of the trials' yields."),
                    "county": st.column_config.NumberColumn(
                        "Their counties", format="%.1f",
                        help="Mean NASS yield of the trials' counties."),
                    "state": st.column_config.NumberColumn("Iowa", format="%.1f"),
                    "over_county": st.column_config.NumberColumn(
                        "Over county", format="%+.1f%%",
                        help="Median of each trial's yield over its county's."),
                    "over_state": st.column_config.NumberColumn(
                        "Over Iowa", format="%+.1f%%",
                        help="Median of each trial's yield over Iowa's."),
                })
        if not HAVE_NASS:
            st.caption("NASS yields come from the shared cache on Snowflake; this database "
                       "has none, so only the trials show.")

    cols = ["year", "county", "district", "trial_type", "trial_detail", "yields",
            "trial_yield", "county_final", "vs_county", "avg_response", "status", "report_url"]
    table = f[cols].sort_values(["year", "county"], ascending=[False, True])
    table["vs_county"] *= 100
    with st.container(border=True):
        st.markdown(f"**Trials** · {len(table):,}")
        st.dataframe(
            table, hide_index=True, height=420,
            column_config={
                "year": st.column_config.NumberColumn("Season", format="%d"),
                "county": "County",
                "district": "District",
                "trial_type": "Trial type",
                "trial_detail": st.column_config.TextColumn("Compared", width="medium"),
                "yields": st.column_config.TextColumn(
                    "Treatment yields", help="Each treatment's average, as the report "
                                             "gives them."),
                "trial_yield": st.column_config.NumberColumn(
                    "Field yield", format="%.1f", help="Mean of the treatment yields."),
                "county_final": st.column_config.NumberColumn("NASS county", format="%.1f"),
                "vs_county": st.column_config.NumberColumn("Over county", format="%+.1f%%"),
                "avg_response": st.column_config.NumberColumn(
                    "ISA response", format="%+.1f",
                    help="The yield response ISA lists for the trial, which each reading "
                         "is checked against."),
                "status": st.column_config.TextColumn(
                    "Read", help="read: yields agree with ISA's response. unread: no "
                                 "reading of the report agreed. no report: the report "
                                 "couldn't be fetched."),
                "report_url": st.column_config.LinkColumn("Report", display_text="PDF"),
            })
        st.download_button("Download CSV", table.to_csv(index=False),
                           file_name=f"isa_strip_trials_{crop.lower()}.csv", mime="text/csv",
                           icon=":material/download:")

    with st.expander("How these are read", icon=":material/info:"):
        st.markdown(
            """
**Source.** ISA's public strip-trial database lists every trial since 2005 with its
county and the yield response ISA measured. Each trial's yields are only in its PDF
report. The loader keeps the text of those reports and reads each one's summary of
treatment averages. The report layout has changed several times since 2005.

**Checked against ISA.** A reading counts only when the gap between its treatments
matches the response ISA lists for the trial. A report no reading agrees with stays
*unread* rather than being guessed at. One example: a "soybean" trial whose report
shows corn-level yields.

**Field yield.** This is the mean of the treatment averages. Treatments mostly move
yield a few bushels, and what's compared with NASS is the field's level.

**Not a random sample.** Cooperators volunteer, often on their better-managed ground.
The trials also move between counties from year to year, and their number has fallen
from about 400 a season to under 100. Read the gap to NASS and how it shifts across
seasons; the level of the trial line says little on its own.

**Internal.** ISA states its copyright and no other terms, so the trial table is not on
the view link, and the report text is not in the app's public repository.
            """)


strip_trials()
