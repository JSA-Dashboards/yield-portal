"""Report text — every report in its own words, laid out like the yield PDF."""
import datetime as dt

import streamlit as st

import data
import report_text as RT

VIEW_ONLY = st.session_state.get("view_only", False)

st.title("Report text")
st.caption("Every report in its own words, laid out like the yield PDF: crop, then state, "
           "with the date it was reported.")

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


def _is_date(d):
    return isinstance(d, dt.date)


years = sorted((int(y) for y in df["crop_year"].dropna().unique()), reverse=True)
with st.sidebar:
    st.subheader("Filters")
    year = st.segmented_control("Crop year", years, default=years[0], key="rt_year") or years[0]
    crops = st.pills("Crop", data.CROPS, selection_mode="multi", default=data.CROPS,
                     key="rt_crops")
    in_year = df[df["crop_year"] == year]
    dates = in_year.loc[in_year["date_reported"].map(_is_date), "date_reported"]
    # these options change with the year, so the year is part of their keys
    states = st.multiselect("States", sorted(in_year["state"].dropna().unique(), key=RT.state_name),
                            format_func=RT.state_name, placeholder="All states",
                            key=f"rt_states_{year}")
    span = st.date_input(
        "Reported", value=[], format="MM/DD/YYYY", key=f"rt_dates_{year}",
        min_value=dates.min() if len(dates) else None,
        max_value=dates.max() if len(dates) else None,
        disabled=dates.empty,
        help="Only reports dated in this range. Leave it empty for every report."
        if len(dates) else f"No {year} report has a date yet. Dates come from the report emails.")
    since = st.date_input(
        "Highlight new since", value=RT.last_report_week(dt.date.today())[0],
        format="MM/DD/YYYY", key="rt_since",
        help="Reports dated on or after this day are in yellow, here and in the Word and PDF "
             "downloads. It starts at the week the last Tuesday email covered: what that email "
             "called new, and everything reported since.")
    order = st.segmented_control("Order within a state", ["Date", "Place"], default="Date",
                                 key="rt_order") or "Date"
    q = st.text_input("Search", placeholder="County, town, or any word", key="rt_q").strip()

if not crops:
    st.warning("Pick a crop in the sidebar.")
    st.stop()

f = in_year[in_year["crop"].isin(crops)]
if states:
    f = f[f["state"].isin(states)]
if span:
    lo, hi = span[0], span[-1]
    f = f[f["date_reported"].map(lambda d: _is_date(d) and lo <= d and (len(span) < 2 or d <= hi))]
if q:
    f = f[f["location"].fillna("").str.contains(q, case=False, regex=False)
          | f["raw_text"].fillna("").str.contains(q, case=False, regex=False)]
if f.empty:
    st.info("No reports match these filters.")
    st.stop()

f = RT.ordered(f, by="place" if order == "Place" else "date")
n_dated = int(f["date_reported"].map(_is_date).sum())
n_new = int(f["date_reported"].map(lambda d: RT.is_new(d, since)).sum())

summary = f"**{len(f):,} reports** · {f['state'].nunique()} states"
if n_dated == 0:
    summary += f" · no report dates for {year} yet"
elif n_dated < len(f):
    summary += f" · {n_dated:,} dated"
if n_new:
    summary += f" · :yellow-background[{n_new:,} new since {since:%b} {since.day}]"


def _subtitle():
    """What the download holds, in words: the filters applied and when it was made."""
    today = dt.date.today()
    parts = [" and ".join(c for c in data.CROPS if c in crops)]
    if states:
        names = sorted(RT.state_name(s) for s in states)
        parts.append(", ".join(names[:4]) + (f" +{len(names) - 4} more" if len(names) > 4 else ""))
    if span:
        parts.append("reported " + " – ".join(f"{d:%b} {d.day}" for d in span))
    if q:
        parts.append(f"matching “{q}”")
    parts += [f"{len(f):,} reports", f"made {today:%b} {today.day}, {today.year}"]
    if n_new:
        parts.append(f"yellow: reported since {since:%b} {since.day}")
    return " · ".join(parts)


with st.container(horizontal=True, horizontal_alignment="distribute",
                  vertical_alignment="center"):
    st.markdown(summary)
    if not VIEW_ONLY:
        title, subtitle = f"Yield reports · {year}", _subtitle()
        stem = f"yield_reports_{year}" + (f"_{crops[0].lower()}" if len(crops) == 1 else "")
        with st.container(horizontal=True, width="content"):
            # built only when clicked, from the reports shown here
            st.download_button(
                "Word", data=lambda: RT.to_docx(f, title, subtitle, since), file_name=f"{stem}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                on_click="ignore", icon=":material/description:")
            st.download_button(
                "PDF", data=lambda: RT.to_pdf(f, title, subtitle, since), file_name=f"{stem}.pdf",
                mime="application/pdf", on_click="ignore", icon=":material/picture_as_pdf:")

for crop, state_rows in RT.sections(f):
    st.header(crop, divider="gray")
    for name, rows in state_rows:
        st.subheader(f"{name} :gray[· {len(rows)}]")
        st.markdown(RT.md_table(rows, q, dates=n_dated > 0, new_since=since))
