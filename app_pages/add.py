"""Add reports — paste a report email (stamped with its date), or key one in, from
Ag Trader Talk, JSA's own, or a seed-company plot a customer shared."""
import datetime as dt

import pandas as pd
import streamlit as st

import data
import db
import parse_pdfs as P

STATES = sorted(P.ABBRS)
ADD, DATE, SKIP = "Add as new", "Date the stored row", "Skip"
PREVIEW_COLS = ["crop", "state", "location", "yield_bpa", "ly_yield", "aph", "maturity",
                "irrigation", "disease", "is_silage", "raw_text"]
today = dt.date.today()

st.title("Add reports")
if msg := st.session_state.pop("add_flash", None):
    st.success(msg, icon=":material/check_circle:")

with st.container(horizontal=True):
    mode = st.segmented_control("Mode", ["Paste an email", "Enter by hand"],
                                default="Paste an email", key="add_mode") or "Paste an email"
    source = st.segmented_control(
        "Source", data.SOURCES, default=data.SOURCES[0], key="add_source",
        help="Ag Trader Talk's report emails, JSA's own reports, or a seed company's plot "
             "result a customer shared. JSA sends some of its reports to Ag Trader Talk too: "
             "when one comes back by email it's matched to the stored report, and a repeat "
             "the matcher misses is flagged as a duplicate on Review & edit.") or data.SOURCES[0]
jsa = source == "JSA"
plot = source == "Seed plot"
company = None
if plot:
    st.caption("A seed company's plot result a customer shared with us (their websites can't "
               "be collected from). Enter the plot's **average across entries** as the yield, "
               "not the best hybrid. Plots are their own source, kept out of the headline "
               "numbers.")
    company = st.selectbox("Company", data.PLOT_COMPANIES, index=None, key="add_company",
                           placeholder="Whose plot", accept_new_options=True)
REPORT_SOURCE = "jsa" if jsa else ("plot" if plot else None)   # None: Ag Trader Talk's


def _val(v):
    """data_editor cell -> plain value (NaN/NA/'' -> None)."""
    if v is None or (not isinstance(v, (list, str)) and pd.isna(v)):
        return None
    if isinstance(v, str) and not v.strip():
        return None
    return v.item() if hasattr(v, "item") else v


if mode == "Paste an email":
    st.caption("Paste the subject and body of a yield report email: one of Ag Trader Talk's, or "
               "JSA's own (set the source above). Each report in it is checked against the "
               "archive and saved with the date you give.")
    with st.form("paste_form"):
        subject = st.text_input("Subject", placeholder="YIELD: Le Sueur Co MN soybeans")
        body = st.text_area("Body", height=170,
                            placeholder="Le Sueur Co MN. 66 bpa, group 2 beans. APH 61")
        c1, c2 = st.columns(2)
        reported = c1.date_input("Date reported", value=today, max_value=today)
        year = c2.number_input("Crop year", min_value=2015, max_value=2100,
                               value=reported.year, step=1)
        go = st.form_submit_button("Check reports", type="primary",
                                   icon=":material/manage_search:")
    if go:
        if not body.strip():
            st.error("Paste the email body first.")
        else:
            st.session_state["add_rows"] = P.parse_email(subject, body, int(year), reported)
            st.session_state["add_meta"] = {"date": reported, "subject": subject.strip() or None}

    rows = st.session_state.get("add_rows")
    meta = st.session_state.get("add_meta", {})
    if rows is not None and not rows:
        st.warning("Nothing left to save once the banner and signature were removed.")
    elif rows:
        archive = data.load_all()
        preview, match_hashes = [], []
        for r in rows:
            kind, h, _ = data.find_match(archive, r)
            if r.get("crop") not in data.CROPS:
                # no crop named in the email: take it from the matched archive row,
                # otherwise leave it for the user to pick
                r["crop"] = (archive.loc[archive["dedup_hash"] == h, "crop"].iloc[0]
                             if h else None)
            if kind == "identical":
                action, note = DATE, "Already stored (same text): " + data.describe(archive, h)
            elif kind == "match":
                action, note = DATE, "Looks like: " + data.describe(archive, h)
            else:
                action, note = ADD, "New report"
            preview.append({"action": action, "archive_check": note,
                            **{c: r.get(c) for c in PREVIEW_COLS}})
            match_hashes.append(h)
        pv = pd.DataFrame(preview)

        with st.container(border=True):
            st.markdown(f"**{len(rows)} report(s) found** — check the fields, pick an action, then save.")
            edited = st.data_editor(
                pv, key="add_editor", hide_index=True, num_rows="fixed",
                column_config={
                    "action": st.column_config.SelectboxColumn(
                        "Action", options=[ADD, DATE, SKIP], required=True, width="medium"),
                    "archive_check": st.column_config.TextColumn(
                        "Archive check", disabled=True, width="large"),
                    "crop": st.column_config.SelectboxColumn("Crop", options=data.CROPS,
                                                             required=True),
                    "state": st.column_config.SelectboxColumn("State", options=STATES),
                    "location": "Location",
                    "yield_bpa": st.column_config.NumberColumn("Yield", format="%.1f"),
                    "ly_yield": st.column_config.NumberColumn("LY", format="%.1f"),
                    "aph": st.column_config.NumberColumn("APH", format="%.0f"),
                    "maturity": "Maturity",
                    "irrigation": st.column_config.SelectboxColumn(
                        "Irrigation", options=data.IRRIGATION),
                    "disease": st.column_config.TextColumn(
                        "Disease / damage", help="Comma-separated, e.g. Tar spot, Hail"),
                    "is_silage": st.column_config.CheckboxColumn("Silage"),
                    "raw_text": st.column_config.TextColumn("Report", width="large"),
                },
            )
            save = st.button("Save", type="primary", icon=":material/save:")

        missing = [i + 1 for i, e in edited.iterrows()
                   if e["action"] == ADD and _val(e["crop"]) not in data.CROPS]
        if save and missing:
            st.error(f"Pick a crop for row {', '.join(map(str, missing))} before saving.")
            save = False
        if save and plot and not company:
            st.error("Pick the plot's company above before saving.")
            save = False
        if save:
            new_rows, dated, kept_earlier, skipped = [], 0, 0, 0
            for i, e in edited.iterrows():
                act, h = e["action"], match_hashes[i]
                if act == SKIP:
                    skipped += 1
                    continue
                if act == DATE and h:
                    stored = archive.loc[archive["dedup_hash"] == h]
                    old = stored["date_reported"].iloc[0] if len(stored) else None
                    if pd.notna(old) and old <= meta["date"]:
                        kept_earlier += 1       # first report date wins
                    else:
                        db.set_reported(h, meta["date"], meta["subject"])
                        dated += 1
                    if REPORT_SOURCE and len(stored) and stored["source"].iloc[0] != source:
                        # one report, kept once; the note says JSA (or the plot) had it too
                        who = f"{company} plot" if plot else source
                        note = f"{who} reported this too ({meta['date']:%b %d, %Y})."
                        prior = stored["notes"].iloc[0]
                        db.update_row(h, {"notes": f"{prior} {note}" if isinstance(prior, str)
                                          and prior.strip() else note})
                    continue
                r = dict(rows[i])
                for c in PREVIEW_COLS:
                    r[c] = _val(e[c])
                r["is_silage"] = bool(r["is_silage"])
                r["dedup_hash"] = P.row_hash(r)    # with its practice, if half of a split
                r.update(report_source=REPORT_SOURCE or "email", date_reported=meta["date"],
                         email_subject=meta["subject"])
                if plot:
                    r["source_file"] = company
                new_rows.append(r)
            inserted, dupes = db.insert_new(new_rows)
            data.invalidate()
            st.session_state.pop("add_rows", None)
            parts = [f"{inserted} added", f"{dated} dated"]
            if kept_earlier:
                parts.append(f"{kept_earlier} already dated (the first report date is kept)")
            if dupes:
                parts.append(f"{dupes} already stored")
            if skipped:
                parts.append(f"{skipped} skipped")
            st.session_state["add_flash"] = "Saved — " + ", ".join(parts) + "."
            st.rerun()

elif plot:
    # a plot is read against its own history, so it comes in as a series: one row a season
    st.caption("Enter the plot's history, one row per season, as the customer shared it: the "
               "same company and place every year. Each season is then read against the plot's "
               "own earlier seasons (5+ needed for a trend) and the same plot last season.")
    with st.form("plot_form"):
        c1, c2, c3 = st.columns([1, 1, 2])
        crop = c1.selectbox("Crop", data.CROPS)
        state = c2.selectbox("State", STATES, index=None, placeholder="Choose")
        location = c3.text_input("Plot location", placeholder="Logan Co, or the nearest town")
        seasons = st.data_editor(
            pd.DataFrame({"season": pd.Series([today.year], dtype="Int64"),
                          "plot_average": pd.Series([None], dtype="Float64"),
                          "entries": pd.Series([None], dtype="Int64"),
                          "notes": pd.Series([""], dtype="string")}),
            num_rows="dynamic", hide_index=True, key="plot_seasons",
            column_config={
                "season": st.column_config.NumberColumn("Season", min_value=1980,
                                                        max_value=today.year, format="%d",
                                                        required=True),
                "plot_average": st.column_config.NumberColumn(
                    "Plot average (bpa)", min_value=0.0, max_value=400.0, format="%.1f",
                    help="The average across the plot's entries, not the best hybrid."),
                "entries": st.column_config.NumberColumn("Entries", min_value=1, format="%d",
                                                         help="Hybrids or varieties in the plot."),
                "notes": st.column_config.TextColumn("Notes", width="large"),
            })
        ok = st.form_submit_button("Save plot history", type="primary", icon=":material/save:")
    if ok:
        good = seasons.dropna(subset=["season", "plot_average"])
        if not company or not state or not location.strip():
            st.error("Pick the company above, and give the plot's state and location.")
        elif good.empty:
            st.error("Add at least one season with its plot average.")
        else:
            rows = []
            for e in good.itertuples(index=False):
                yr, avg = int(e.season), float(e.plot_average)
                note = str(e.notes).strip() if isinstance(e.notes, str) else ""
                raw = (f"{company} plot, {location.strip()}, {state}: plot average {avg:g} bpa"
                       + (f" ({int(e.entries)} entries)" if pd.notna(e.entries) else "")
                       + (f". {note}" if note else ""))
                row = {"crop_year": yr, "crop": crop, "state": state,
                       "location": location.strip(), "yield_bpa": avg, "yield_min": avg,
                       "yield_max": avg, "raw_text": raw, "report_source": "plot",
                       "source_file": company, "is_silage": False, "is_record": False,
                       # history has no report date; this season's is dated today
                       "date_reported": today if yr == today.year else None}
                row["dedup_hash"] = P.dedup_hash(yr, crop, state, row["location"], raw)
                rows.append(row)
            inserted, dupes = db.insert_new(rows)
            data.invalidate()
            st.session_state["add_flash"] = (
                f"Saved {inserted} season(s) of the {company} plot at {location.strip()}"
                + (f"; {dupes} were already stored" if dupes else "") + ".")
            st.rerun()

else:
    st.caption("Key in a report that didn't arrive by email or PDF: JSA's own, or one of "
               "Ag Trader Talk's (set the source above). A seed plot comes in as its whole "
               "history: pick Seed plot above.")
    with st.form("manual_form"):
        c1, c2, c3 = st.columns(3)
        year = c1.number_input("Crop year", min_value=2015, max_value=2100,
                               value=today.year, step=1)
        reported = c2.date_input("Date reported", value=today, max_value=today)
        crop = c3.selectbox("Crop", data.CROPS)
        c4, c5 = st.columns([1, 3])
        state = c4.selectbox("State", STATES, index=None, placeholder="Choose")
        location = c5.text_input("Location", placeholder="Morgan Co")
        c6, c7, c8, c9 = st.columns(4)
        yld = c6.number_input("Yield (bpa)", min_value=0.0, max_value=400.0, value=None,
                              help="Several fields, or dryland and irrigated figures, in one "
                                   "report? Leave Yield and Irrigation empty: it's saved as one "
                                   "report per entry, each with its figures from the text.")
        ly = c7.number_input("Last year (bpa)", min_value=0.0, max_value=400.0, value=None)
        aph = c8.number_input("APH", min_value=0.0, max_value=400.0, value=None)
        maturity = c9.text_input("Maturity", placeholder="110 or 2.6")
        irrigation = st.pills("Irrigation", data.IRRIGATION)
        disease = st.multiselect("Disease / damage", data.DISEASE_OPTIONS)
        c10, c11 = st.columns(2)
        is_silage = c10.checkbox("Silage appraisal")
        is_record = c11.checkbox("Record yield")
        text = st.text_area("Report text", placeholder="The report as written — kept as the original.")
        ok = st.form_submit_button("Save report", type="primary", icon=":material/save:")
    if ok:
        if not state or not (location.strip() or text.strip()):
            st.error("A state and either a location or the report text are required.")
        else:
            raw = text.strip() or (f"{location.strip()}, {state}: "
                                   + (f"{yld:g} bpa" if yld is not None else "no yield given"))
            row = {"crop_year": int(year), "date_reported": reported, "crop": crop,
                   "state": state, "location": location.strip() or None,
                   "yield_bpa": yld, "yield_min": yld, "yield_max": yld, "ly_yield": ly,
                   "aph": aph, "maturity": maturity.strip() or None,
                   "irrigation": irrigation, "disease": ", ".join(disease) or None,
                   "is_silage": is_silage, "is_record": is_record, "raw_text": raw,
                   "report_source": REPORT_SOURCE or "manual"}
            row["dedup_hash"] = P.dedup_hash(row["crop_year"], crop, state,
                                             row["location"], raw)
            rows = [row]
            halves = (P.by_entry(dict(row, irrigation=None))
                      if yld is None and irrigation in (None, "Mixed") else [])
            if len(halves) > 1:
                # several fields, or dryland and irrigated: one report per entry, read
                # from the text; what was keyed in still wins
                keyed = {"ly_yield": ly, "aph": aph, "maturity": row["maturity"],
                         "disease": row["disease"]}
                for k in halves:
                    k.update({f: v for f, v in keyed.items() if v is not None})
                    k["is_silage"] = k["is_silage"] or is_silage
                    k["is_record"] = k["is_record"] or is_record
                rows = halves
            _, twin, _ = data.find_match(data.load_all(), rows[0])
            twin_text = data.describe(data.load_all(), twin) if twin else ""
            inserted, _ = db.insert_new(rows)
            data.invalidate()
            if len(rows) > 1 and inserted:
                kind = {"Non-irrigated": "dryland", "Irrigated": "irrigated"}
                st.success(f"Saved as {len(rows)} reports, one per entry: " + "; ".join(
                    f"{kind.get(k['irrigation'], 'field')} "
                    + (f"{k['yield_min']:g}–{k['yield_max']:g}" if k["yield_max"] != k["yield_min"]
                       else f"{k['yield_bpa']:g}") + " bpa" for k in rows) + ".",
                    icon=":material/call_split:")
            elif not inserted:
                st.warning("That exact report is already in the archive — nothing added.")
            elif twin:
                st.success("Report saved.", icon=":material/check_circle:")
                st.info(f"It may repeat a stored report ({twin_text}). If it's the same one, "
                        "keep one on **Review & edit**: it's flagged there when the place and "
                        "yield match.", icon=":material/content_copy:")
            else:
                st.success("Report saved.", icon=":material/check_circle:")
