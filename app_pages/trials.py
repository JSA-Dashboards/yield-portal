"""Variety trials — the university test-plot archive, beside the field reports."""
import altair as alt
import pandas as pd
import streamlit as st

import data
import trials

VIEW_ONLY = st.session_state.get("view_only", False)
BRAND = "#0693e3"
STATE_INK = "#1baf7a"
TREND_INK = "#9aa0a6"
BAR = dict(cornerRadiusEnd=3)
CATEGORY_GAP = alt.Scale(paddingInner=0.3)

st.title("Variety trials")
st.caption("What the state universities published at each test site, 2000 onward — "
           "the replicated plot yields the field reports can be read against.")

if VIEW_ONLY:                       # belt and braces: not registered on the view link
    st.stop()

try:
    sites, states = trials.load_all()
except trials.NotLoaded:
    st.info("The trial tables aren't loaded in this database yet.",
            icon=":material/database:")
    st.code("python load_trials.py --target snowflake", language="bash")
    st.caption("Run `python export_portal_tables.py` in the illinois-corn-trials "
               "project first; it writes the two CSVs this reads.")
    st.stop()
except Exception as exc:                  # e.g. the database login is refused
    st.error(f"Couldn't read the trial tables: {exc}", icon=":material/cloud_off:")
    st.stop()

# Colour follows the programme, fixed for the whole archive, so changing the filter
# never repaints the series that remain. Eight programmes, eight validated slots.
SERIES_ORDER = ["IL", "IA", "MO", "OH", "NE-IR", "NE-RF", "WI", "MI"]
STATE_SCALE = alt.Scale(domain=SERIES_ORDER, range=data.YEAR_PALETTE)
NAME_OF = dict(zip(sites.state, sites.state_name))

yields_tab, state_tab, sources_tab = st.tabs(
    ["Site yields", "Against the state crop", "Programmes & caveats"])


@st.fragment
def site_yields():
    """One row per location per season, filtered and charted."""
    all_states = [s for s in SERIES_ORDER if s in set(sites.state)]
    # off by default: three series run different locations early and late, so a line
    # through them falls without a season being bad (see the comparable flag)
    steady = [s for s in all_states
              if sites[(sites.state == s) & sites.comparable].shape[0]]
    with st.container(horizontal=True):
        crop = st.segmented_control("Crop", ["Corn", "Soybeans"], default="Corn",
                                    key="tr_crop") or "Corn"
        picked = st.pills("Programmes", all_states, selection_mode="multi",
                          default=steady, key="tr_states",
                          format_func=lambda s: NAME_OF.get(s, s))
    f = sites[sites.crop_label == crop]
    if picked:
        f = f[f.state.isin(picked)]
    span = (int(sites.year.min()), int(sites.year.max()))
    lo, hi = st.slider("Seasons", span[0], span[1], span, key="tr_years")
    f = f[(f.year >= lo) & (f.year <= hi)]
    q = st.text_input("Search locations", placeholder="Site or region",
                      key="tr_q", label_visibility="collapsed")
    if q:
        f = f[f.site.str.contains(q, case=False, regex=False)
              | f.group_name.fillna("").str.contains(q, case=False, regex=False)]
    if f.empty:
        st.info("No site-years match these filters.")
        return
    shifting = sorted(set(f.loc[~f.comparable, "state_name"]))
    if shifting:
        st.warning(
            "%s tests different locations early and late in its run, so a line through "
            "it moves with the sites rather than the season. The site-years are real; "
            "the slope is not." % ", ".join(shifting), icon=":material/warning:")

    latest = int(f.year.max())
    cur = f[f.year == latest]
    with st.container(horizontal=True):
        st.metric("Site-years", f"{len(f):,}", border=True,
                  help="One location in one season, as that programme published it.")
        st.metric("Locations", f"{f.site.nunique():,}", border=True,
                  help="Distinct test sites across the selected programmes.")
        st.metric(f"Mean site yield · {latest}", f"{cur.yield_mean.mean():.1f} bu",
                  border=True,
                  help="Unweighted mean of the selected sites in the latest season "
                       "shown. The state comparison weights by programme instead.")
        st.metric("Varieties tested", f"{int(f.entries.sum()):,}" if f.entries.notna().any()
                  else "—", border=True,
                  help="Entries summed over the selected site-years. Iowa after 2013 "
                       "and Illinois publish no entry count, so this undercounts them.")

    by_year = (f.groupby(["state", "year"], as_index=False)
                .agg(site_yield=("yield_mean", "mean"), sites=("site", "nunique")))
    by_year["year"] = by_year.year.astype(int)
    by_year["state_name"] = by_year.state.map(NAME_OF)
    with st.container(border=True):
        head, toggle = st.columns([3, 1], vertical_alignment="center")
        head.markdown(f"**{crop} site yields by season**")
        view = toggle.segmented_control("View", ["Chart", "Table"], default="Chart",
                                        key="tr_view", label_visibility="collapsed") or "Chart"
        if view == "Chart":
            st.caption("Mean of the selected sites in each programme. Programmes test "
                       "different locations, so the levels are not comparable between "
                       "them — the slopes are.")
            hover = alt.selection_point(on="pointerover", nearest=True, fields=["year"],
                                        empty=False)
            base = alt.Chart(by_year).encode(
                x=alt.X("year:Q", title=None, axis=alt.Axis(format="d"),
                        scale=alt.Scale(nice=False)),
                y=alt.Y("site_yield:Q", title=f"{crop} yield (bu/acre)",
                        scale=alt.Scale(zero=False)),
                color=alt.Color("state:N", title="Programme", scale=STATE_SCALE,
                                legend=alt.Legend(labelExpr="datum.label")),
            )
            line = base.mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=36))
            marks = base.mark_circle(size=90, opacity=0).add_params(hover)
            tips = base.mark_circle(size=110, stroke="#ffffff", strokeWidth=2).encode(
                opacity=alt.condition(hover, alt.value(1), alt.value(0)),
                tooltip=[alt.Tooltip("state_name:N", title="Programme"),
                         alt.Tooltip("year:Q", title="Season", format="d"),
                         alt.Tooltip("site_yield:Q", title="Mean site yield",
                                     format=".1f"),
                         alt.Tooltip("sites:Q", title="Sites")],
            )
            st.altair_chart(alt.layer(line, marks, tips).properties(height=340))
        else:
            wide = by_year.pivot(index="state_name", columns="year", values="site_yield")
            wide = wide[sorted(wide.columns, reverse=True)]
            wide.columns = [str(c) for c in wide.columns]
            st.dataframe(
                wide.reset_index().rename(columns={"state_name": "Programme"}),
                hide_index=True,
                column_config={c: st.column_config.NumberColumn(c, format="%.1f")
                               for c in wide.columns})

    cols = ["year", "state_name", "crop_label", "site", "grouping", "group_name",
            "yield_mean", "entries", "tests", "irrigation", "comparable", "programme"]
    table = f[cols].sort_values(["year", "state_name", "site"],
                                ascending=[False, True, True])
    with st.container(border=True):
        st.markdown(f"**Site-years** · {len(table):,}")
        st.dataframe(
            table, hide_index=True, height=420,
            column_config={
                "year": st.column_config.NumberColumn("Season", format="%d"),
                "state_name": "Programme",
                "crop_label": "Crop",
                "site": "Location",
                "grouping": st.column_config.TextColumn(
                    "Grouped by", help="What this programme calls its grouping: "
                                       "region, district, or irrigation regime."),
                "group_name": "Group",
                "yield_mean": st.column_config.NumberColumn(
                    "Site yield", format="%.1f",
                    help="Mean of every entry at that site, as published."),
                "entries": st.column_config.NumberColumn(
                    "Entries", format="%.0f", help="Hybrids or varieties in the test."),
                "tests": st.column_config.NumberColumn(
                    "Tables", format="%.0f",
                    help="Tables pooled into the site-year. Missouri only."),
                "irrigation": st.column_config.TextColumn(
                    "Irrigation", help="As the programme designated it. Blank where "
                                       "it published nothing either way."),
                "programme": st.column_config.TextColumn("Source", width="medium"),
                "comparable": st.column_config.CheckboxColumn(
                    "Trendable", help="Off where the programme's locations change so "
                                      "much across its run that a fitted trend would "
                                      "track composition instead of the season."),
            })
        st.download_button("Download CSV", table.to_csv(index=False),
                           file_name=f"trial_site_years_{crop.lower()}.csv",
                           mime="text/csv", icon=":material/download:")


@st.fragment
def against_state():
    """One programme at a time against its USDA state yield."""
    pairs = (states[["state", "state_name", "crop", "crop_label", "trend_rate", "corr",
                     "n_paired"]].drop_duplicates(subset=["state", "crop"]))
    pairs["key"] = pairs.state + " · " + pairs.crop_label
    order = {s: i for i, s in enumerate(SERIES_ORDER)}
    pairs = pairs.sort_values(["crop", "state"], key=lambda c: c.map(order)
                              if c.name == "state" else c)
    choice = st.selectbox("Series", pairs.key.tolist(), key="tr_series",
                          format_func=lambda k: "%s — %s" % (
                              pairs.loc[pairs.key == k, "state_name"].iloc[0],
                              pairs.loc[pairs.key == k, "crop_label"].iloc[0]))
    row = pairs[pairs.key == choice].iloc[0]
    d = states[(states.state == row.state) & (states.crop == row.crop)].copy()
    d["year"] = d.year.astype(int)
    d = d.sort_values("year")

    with st.container(horizontal=True):
        st.metric("Seasons", f"{len(d)}", border=True,
                  help=f"{int(d.year.min())}–{int(d.year.max())}")
        st.metric("Fitted gain", f"{row.trend_rate:+.2f} bu/yr", border=True,
                  help="Least-squares slope of the programme's own average over the "
                       "whole series. Genetic gain in the Corn Belt runs about "
                       "2 bu/acre a year for corn and 0.5 for soybeans.")
        st.metric("Trial over state", f"{d.spread.mean():+.1f} bu", border=True,
                  help="Average gap between the trial mean and the USDA state yield. "
                       "Test plots beat the state crop by construction — small plots, "
                       "best ground, no harvest loss — so the level of the gap is not "
                       "news. Its movement is.")
        corr = row["corr"]            # row.corr would be Series.corr, the method
        st.metric("Year-over-year correlation",
                  f"{corr:.2f}" if pd.notna(corr) else "—", border=True,
                  help=f"Trial change against state change over "
                       f"{int(row.n_paired) if pd.notna(row.n_paired) else 0} paired "
                       f"seasons. Above about 0.6 the trials are tracking the weather, "
                       f"not just the genetics.")

    with st.container(border=True):
        head, toggle = st.columns([3, 1], vertical_alignment="center")
        head.markdown(f"**{row.state_name} {row.crop_label.lower()}: trials, "
                      f"state crop and the fitted trend**")
        view = toggle.segmented_control("View", ["Chart", "Table"], default="Chart",
                                        key="tr_vs_view",
                                        label_visibility="collapsed") or "Chart"
        if view == "Chart":
            long = pd.concat([
                d[["year", "trial_yield"]].rename(columns={"trial_yield": "value"})
                 .assign(series="Trial average"),
                d[["year", "state_yield"]].rename(columns={"state_yield": "value"})
                 .assign(series="USDA state yield"),
                d[["year", "trend"]].rename(columns={"trend": "value"})
                 .assign(series="Fitted trend"),
            ]).dropna(subset=["value"])
            scale = alt.Scale(domain=["Trial average", "USDA state yield",
                                      "Fitted trend"],
                              range=[BRAND, STATE_INK, TREND_INK])
            hover = alt.selection_point(on="pointerover", nearest=True,
                                        fields=["year"], empty=False)
            base = alt.Chart(long).encode(
                x=alt.X("year:Q", title=None, axis=alt.Axis(format="d"),
                        scale=alt.Scale(nice=False)),
                y=alt.Y("value:Q", title="Yield (bu/acre)",
                        scale=alt.Scale(zero=False)),
                color=alt.Color("series:N", title=None, scale=scale),
            )
            solid = base.transform_filter(
                alt.datum.series != "Fitted trend").mark_line(strokeWidth=2)
            dashed = base.transform_filter(
                alt.datum.series == "Fitted trend").mark_line(
                    strokeWidth=1.5, strokeDash=[4, 3])
            # the two real series are also labelled at their last point, so identity
            # never rests on colour alone
            ends = (long[long.series != "Fitted trend"]
                    .sort_values("year").groupby("series", as_index=False).tail(1))
            labels = alt.Chart(ends).mark_text(
                align="left", dx=6, dy=-2, fontSize=11, fontWeight=600,
            ).encode(x="year:Q", y="value:Q",
                     text=alt.Text("series:N"),
                     color=alt.Color("series:N", scale=scale, legend=None))
            marks = base.mark_circle(size=90, opacity=0).add_params(hover)
            tips = base.mark_circle(size=110, stroke="#ffffff", strokeWidth=2).encode(
                opacity=alt.condition(hover, alt.value(1), alt.value(0)),
                tooltip=[alt.Tooltip("series:N", title=None),
                         alt.Tooltip("year:Q", title="Season", format="d"),
                         alt.Tooltip("value:Q", title="Yield", format=".1f")])
            # right padding so the direct labels at the last point are not clipped
            st.altair_chart(alt.layer(solid, dashed, labels, marks, tips)
                            .properties(height=360, padding={"right": 96})
                            .resolve_scale(color="shared"))
            st.caption("Same axis, same units. The trial line sits above the state "
                       "crop in every season; what the comparison is for is whether "
                       "the two move together.")
        else:
            st.dataframe(
                d[["year", "sites", "entries", "trial_yield", "state_yield", "spread",
                   "trial_chg", "state_chg", "pct_of_trend"]],
                hide_index=True, height=360,
                column_config={
                    "year": st.column_config.NumberColumn("Season", format="%d"),
                    "sites": st.column_config.NumberColumn("Sites", format="%.0f"),
                    "entries": st.column_config.NumberColumn("Entries", format="%.0f"),
                    "trial_yield": st.column_config.NumberColumn("Trials", format="%.1f"),
                    "state_yield": st.column_config.NumberColumn("State", format="%.1f"),
                    "spread": st.column_config.NumberColumn("Gap", format="%+.1f"),
                    "trial_chg": st.column_config.NumberColumn("Trials YoY",
                                                               format="%+.1f"),
                    "state_chg": st.column_config.NumberColumn("State YoY",
                                                               format="%+.1f"),
                    "pct_of_trend": st.column_config.NumberColumn(
                        "% of trend", format="%.1f",
                        help="The season's trial average as a percentage of the "
                             "fitted trend — the programme's own read on the year."),
                })

    with st.container(border=True):
        st.markdown("**Every series**")
        st.caption("Fitted gain and year-over-year correlation for each programme, "
                   "so a single state's figure can be read against the rest.")
        allp = pairs.copy()
        allp["state_name"] = allp.state.map(NAME_OF).fillna(allp.state_name)
        st.dataframe(
            allp[["state_name", "crop_label", "trend_rate", "corr", "n_paired"]],
            hide_index=True,
            column_config={
                "state_name": "Programme",
                "crop_label": "Crop",
                "trend_rate": st.column_config.NumberColumn("Fitted gain (bu/yr)",
                                                            format="%+.2f"),
                "corr": st.column_config.NumberColumn("YoY correlation", format="%.2f"),
                "n_paired": st.column_config.NumberColumn("Paired seasons",
                                                          format="%.0f"),
            })


@st.fragment
def sources():
    cover = (sites.groupby(["state_name", "crop_label"], as_index=False)
             .agg(first=("year", "min"), last=("year", "max"),
                  site_years=("year", "size"), locations=("site", "nunique")))
    st.dataframe(
        cover.sort_values(["state_name", "crop_label"]), hide_index=True,
        column_config={
            "state_name": "Programme", "crop_label": "Crop",
            "first": st.column_config.NumberColumn("From", format="%d"),
            "last": st.column_config.NumberColumn("To", format="%d"),
            "site_years": st.column_config.NumberColumn("Site-years", format="%d"),
            "locations": st.column_config.NumberColumn("Locations", format="%d"),
        })
    st.markdown(
        """
**What these numbers are.** Each row is one location in one season: the mean of every
hybrid or variety entered in that programme's replicated test there, as the university
published it. Where a report printed its own mean, that figure is used; where it did not,
it is reconstructed from the entry rows and checked against whatever the report *does*
print.

**Levels are not comparable between programmes, slopes are.** Each university tests its
own locations on its own ground, and test plots beat the state crop by construction —
small plots, best ground, no harvest loss. The useful readings are a programme's slope
over time, and whether its year-over-year move matches the state crop's.

**Missouri is dryland only.** MU runs an irrigated and a non-irrigated corn test side by
side at several locations, so publishing both pooled made a site-year a blend. Only the
non-irrigated test is kept, which drops the southeast delta and Laddonia. For soybeans MU
has no irrigated test at all — water is recorded per location, and 2010 and 2018 onward
record nothing either way, so delta locations in those seasons are excluded as
undetermined.

**Nebraska is split by regime**, irrigated and dryland, because UNL runs and publishes
them separately. USDA stopped publishing the matching state split after 2018, so both are
compared against the whole-state series.

**Michigan corn stops at 2022** and its fitted line is flat — the programme's own
ten-year path, not genetic gain. Wisconsin corn stops at 2023. Both programmes stopped
publishing; the soybean series continue.

**Indiana is absent by licence.** Purdue Research Foundation permits reproducing its
tables only whole and unmanipulated, so a derived cross-state series is not a licensed
use. Minnesota, North Dakota, Kansas and South Dakota are blocked on the same ground.

**Ohio is published here but not cleared for outside use.** Ohio State has not yet given
written permission for derived use of its Corn Performance Test, so this page is internal
only — it is not on the read-only share link, and the trial tables are not in the app's
public repository.
        """)


with yields_tab:
    site_yields()
with state_tab:
    against_state()
with sources_tab:
    sources()
