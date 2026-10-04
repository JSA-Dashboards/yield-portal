"""Review & edit — the review queue (reports the checks flagged) and the full
table for hand edits."""
import streamlit as st

import checks
import data
import db
import parse_pdfs as P
import report_text as RT

STATES = sorted(P.ABBRS)
EDIT_COLS = ["crop_year", "date_reported", "crop", "state", "location", "yield_bpa",
             "yield_min", "yield_max", "ly_yield", "expected_yield", "aph", "maturity",
             "irrigation", "disease", "is_silage", "is_record", "raw_text", "notes"]
SHORT = {"duplicate": "Possible duplicate", "corn?": "Probably corn",
         "reread": "Parser reads it differently", "range": "Out of range"}
STATUS = {"clean": "", "flagged": "Needs review", "approved": "Approved", "excluded": "Excluded"}

st.title("Review & edit")
if msg := st.session_state.pop("rev_flash", None):
    st.success(msg, icon=":material/check_circle:")

df = data.load_all()
if df.empty:
    st.info("No reports yet.")
    st.stop()
queue_all = df[df["status"] == "flagged"]


def _who():
    return (st.session_state.get("rev_who") or "").strip() or "portal"


def _done(message):
    """After any write: fresh data, a new (unselected) queue table, a note."""
    data.invalidate()
    st.session_state["rev_gen"] = st.session_state.get("rev_gen", 0) + 1
    st.session_state["rev_flash"] = message
    st.rerun()


def _approve(r, clear, note):
    """Approve a report for the given checks, keeping what was approved before.
    An excluded report stays excluded (a hand edit doesn't bring it back)."""
    if r.get("decision") == "excluded":
        return
    keep = sorted(set(r["approved_flags"]) | set(clear))
    db.set_decision(r["dedup_hash"], "approved", keep, note, _who())


def _place(r):
    return ", ".join(str(v) for v in (r["location"], r["state"]) if isinstance(v, str) and v)


mode = st.segmented_control(
    "Show", ["queue", "all"], default="queue" if len(queue_all) else "all", key="rev_mode",
    format_func=lambda k: f"Needs review ({len(queue_all)})" if k == "queue" else "All reports",
    label_visibility="collapsed") or "queue"

# --- the review queue -----------------------------------------------------------
if mode == "queue":
    st.caption("Reports the checks flagged. They stay in the archive and on Report text, but "
               "aren't counted on Explore until you approve, fix or exclude them. Nothing is "
               "ever deleted here.")
    if queue_all.empty:
        st.success("Nothing waiting for review.", icon=":material/task_alt:")
        st.stop()

    counts = {k: int(queue_all["review_flags"].map(lambda f, k=k: k in f).sum()) for k in SHORT}
    with st.container(horizontal=True, vertical_alignment="bottom"):
        kinds = st.pills("Checks", [k for k in SHORT if counts[k]], selection_mode="multi",
                         format_func=lambda k: f"{SHORT[k]} ({counts[k]})", key="rev_kinds")
        st.text_input("Reviewed by", placeholder="Your initials", key="rev_who", width=160)
    queue = queue_all
    if kinds:
        queue = queue[queue["review_flags"].map(lambda f: bool(set(f) & set(kinds)))]
    queue = queue.sort_values(["crop_year", "state", "location"], ascending=[False, True, True],
                              na_position="last").reset_index(drop=True)

    fixable = queue[queue["suggestion"].notna()
                    & queue["review_flags"].map(lambda f: set(f) <= {"corn?", "reread"})]
    if len(fixable):
        with st.expander(f"Apply all {len(fixable)} suggested fixes in this list"):
            st.caption("Only reports whose every flag comes with a fix (probably corn, parser "
                       "re-reads). Each gets its fix and is approved. Look the list over first.")
            ok = st.checkbox("I've checked the suggested fixes below", key="rev_bulk_ok")
            if st.button(f"Apply {len(fixable)} fixes", icon=":material/done_all:",
                         disabled=not ok):
                for r in fixable.to_dict("records"):
                    db.update_row(r["dedup_hash"], r["suggestion"])
                    _approve(r, r["review_flags"], "suggested fix applied (bulk)")
                _done(f"Applied {len(fixable)} suggested fixes.")

    table = queue.assign(
        place=queue.apply(_place, axis=1),
        checks=queue["review_flags"].map(lambda f: ", ".join(SHORT[k] for k in f)),
        fix=queue["suggestion"].map(checks.describe_fix),
    )[["crop_year", "crop", "place", "yield_bpa", "checks", "fix", "raw_text"]]
    event = st.dataframe(
        table, hide_index=True, height=320, on_select="rerun", selection_mode="single-row",
        key=f"rev_queue_{st.session_state.get('rev_gen', 0)}",
        column_config={
            "crop_year": st.column_config.NumberColumn("Year", format="%d", width="small"),
            "crop": st.column_config.TextColumn("Crop", width="small"),
            "place": "Place",
            "yield_bpa": st.column_config.NumberColumn("Yield", format="%.1f", width="small"),
            "checks": "Flagged for",
            "fix": "Suggested fix",
            "raw_text": st.column_config.TextColumn("Report", width="large"),
        })
    rows = event.selection.rows
    if not rows:
        st.caption(":material/arrow_upward: Select a report to review it.")
        st.stop()

    r = queue.iloc[rows[0]].to_dict()
    y = f"{r['yield_bpa']:g} bpa" if r["yield_bpa"] == r["yield_bpa"] and r["yield_bpa"] is not None \
        else "no yield"
    with st.container(border=True):
        st.markdown(f"**{r['crop_year']} {r['crop']} · {_place(r) or 'no place'} · {y}**")
        for k in r["review_flags"]:
            st.markdown(f":orange-badge[{SHORT[k]}] {checks.CHECKS[k]}")
        st.markdown(RT.md_report(r["raw_text"], r["location"], r["state"]))
        others = df[df["dedup_hash"].isin(r["dup_of"])]
        if len(others):
            st.caption("The other report(s) with the same place, crop, year and yield:")
            for o in others.to_dict("records"):
                st.markdown("> " + RT.md_report(o["raw_text"], o["location"], o["state"]))
        if r["suggestion"]:
            st.markdown(f"**Suggested fix:** {checks.describe_fix(r['suggestion'])}")

        with st.container(horizontal=True):
            if r["suggestion"]:
                if st.button("Apply fix", type="primary", icon=":material/auto_fix_high:"):
                    db.update_row(r["dedup_hash"], r["suggestion"])
                    _approve(r, r["review_flags"], "suggested fix applied")
                    _done("Fix applied and approved.")
            if len(others):
                if st.button("Keep both: different reports", icon=":material/call_split:"):
                    for o in [r] + others.to_dict("records"):
                        _approve(o, ["duplicate"], "kept: not the same report")
                    _done("Kept both as separate reports.")
            if st.button("Approve as it is", icon=":material/check:"):
                _approve(r, r["review_flags"], "approved as it is")
                _done("Approved.")
            if st.button("Exclude", icon=":material/block:",
                         help="Keeps the report in the archive (Report text) but out of "
                              "every average and chart."):
                db.set_decision(r["dedup_hash"], "excluded", r["review_flags"],
                                "excluded in review", _who())
                _done("Excluded from averages; still in the archive.")

        with st.expander("Edit by hand"):
            with st.form("rev_edit", border=False):
                c1, c2, c3 = st.columns([1, 1, 2])
                crop = c1.selectbox("Crop", data.CROPS, index=data.CROPS.index(r["crop"])
                                    if r["crop"] in data.CROPS else 0)
                state = c2.selectbox("State", STATES, index=STATES.index(r["state"])
                                     if r["state"] in STATES else None)
                location = c3.text_input("Place", value=r["location"] or "")
                c4, c5, c6 = st.columns(3)

                def _num(v):
                    return None if v is None or v != v else float(v)
                yld = c4.number_input("Yield (bpa)", value=_num(r["yield_bpa"]), step=1.0)
                ly = c5.number_input("Last year (bpa)", value=_num(r["ly_yield"]), step=1.0)
                aph = c6.number_input("APH (bpa)", value=_num(r["aph"]), step=1.0)
                if st.form_submit_button("Save and approve", type="primary"):
                    db.update_row(r["dedup_hash"], {"crop": crop, "state": state,
                                                    "location": location or None,
                                                    "yield_bpa": yld, "ly_yield": ly, "aph": aph})
                    _approve(r, set(r["review_flags"]) | {"reread"}, "edited by hand in review")
                    _done("Saved and approved.")
    st.stop()

# --- every report, for hand edits ---------------------------------------------------
with st.container(horizontal=True, vertical_alignment="bottom"):
    years = sorted(int(y) for y in df["crop_year"].dropna().unique())
    year = st.selectbox("Crop year", ["All"] + years, key="rev_year")
    crop = st.segmented_control("Crop", ["All"] + data.CROPS, default="All",
                                key="rev_crop") or "All"
    states = st.multiselect("States", sorted(df["state"].dropna().unique()),
                            placeholder="All states", key="rev_states")
    q = st.text_input("Search", placeholder="Any word", key="rev_q")

f = df
if year != "All":
    f = f[f["crop_year"] == year]
if crop != "All":
    f = f[f["crop"] == crop]
if states:
    f = f[f["state"].isin(states)]
if q:
    f = f[f["location"].fillna("").str.contains(q, case=False, regex=False)
          | f["raw_text"].fillna("").str.contains(q, case=False, regex=False)]

f = f.sort_values(["crop_year", "state", "location"], ascending=[False, True, True],
                  na_position="last").reset_index(drop=True)
hashes = f["dedup_hash"].tolist()
show = f[EDIT_COLS].copy()
show.insert(0, "check", f["status"].map(STATUS))
show.insert(0, "delete", False)

st.caption(f"{len(show):,} reports. Edit cells and save before changing filters — "
           "unsaved edits are dropped when the list changes. A hand edit counts as reviewed: "
           "the parser won't suggest changing it back.")
edited = st.data_editor(
    show, key="rev_editor", hide_index=True, num_rows="fixed", height=540,
    column_config={
        "delete": st.column_config.CheckboxColumn("Delete", width="small"),
        "check": st.column_config.TextColumn("Check", disabled=True, width="small"),
        "crop_year": st.column_config.NumberColumn("Year", format="%d", step=1),
        "date_reported": st.column_config.DateColumn("Reported", format="MMM D, YYYY"),
        "crop": st.column_config.SelectboxColumn("Crop", options=data.CROPS, required=True),
        "state": st.column_config.SelectboxColumn("State", options=STATES),
        "location": "Location",
        "yield_bpa": st.column_config.NumberColumn("Yield", format="%.1f"),
        "yield_min": st.column_config.NumberColumn("Low", format="%.1f"),
        "yield_max": st.column_config.NumberColumn("High", format="%.1f"),
        "ly_yield": st.column_config.NumberColumn("LY", format="%.1f"),
        "expected_yield": st.column_config.NumberColumn("Expected", format="%.1f"),
        "aph": st.column_config.NumberColumn("APH", format="%.0f"),
        "maturity": "Maturity",
        "irrigation": st.column_config.SelectboxColumn("Irrigation", options=data.IRRIGATION),
        "disease": st.column_config.TextColumn("Disease / damage",
                                               help="Comma-separated, e.g. Tar spot, Hail"),
        "is_silage": st.column_config.CheckboxColumn("Silage"),
        "is_record": st.column_config.CheckboxColumn("Record"),
        "raw_text": st.column_config.TextColumn("Report", width="large"),
        "notes": st.column_config.TextColumn("Notes", width="medium"),
    },
)

changes = st.session_state.get("rev_editor", {}).get("edited_rows", {})
edits = {pos: {k: v for k, v in vals.items() if k not in ("delete", "check")}
         for pos, vals in changes.items()}
edits = {pos: vals for pos, vals in edits.items() if vals}
to_delete = [hashes[pos] for pos, vals in changes.items() if vals.get("delete")]

with st.container(horizontal=True, vertical_alignment="center"):
    save = st.button(f"Save {len(edits)} edited row(s)", type="primary",
                     icon=":material/save:", disabled=not edits)
    if to_delete:
        confirm = st.checkbox(f"Yes, permanently delete {len(to_delete)} row(s)")
        delete = st.button("Delete", icon=":material/delete:", disabled=not confirm)
    else:
        delete = False

if save:
    n = 0
    for pos, vals in edits.items():
        # empty cells come back as None; dates as ISO strings — both bind as-is
        vals = {k: (None if isinstance(v, str) and not v.strip() else v)
                for k, v in vals.items()}
        n += db.update_row(hashes[pos], vals)
        # a person set these values: the parser's re-read must not undo them
        _approve(f.iloc[pos].to_dict(), ["reread"], "edited in the table")
    data.invalidate()
    st.session_state["rev_flash"] = f"Saved {n} row(s)."
    st.rerun()

if delete:
    n = sum(db.delete_row(h) for h in to_delete)
    data.invalidate()
    st.session_state["rev_flash"] = f"Deleted {n} row(s)."
    st.rerun()
