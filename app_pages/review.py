"""Review & edit — fix what the parser missed, set dates, remove bad rows."""
import pandas as pd
import streamlit as st

import data
import db
import parse_pdfs as P

STATES = sorted(P.ABBRS)
EDIT_COLS = ["crop_year", "date_reported", "crop", "state", "location", "yield_bpa",
             "yield_min", "yield_max", "ly_yield", "expected_yield", "aph", "maturity",
             "irrigation", "disease", "is_silage", "is_record", "raw_text", "notes"]

st.title("Review & edit")
if msg := st.session_state.pop("rev_flash", None):
    st.success(msg, icon=":material/check_circle:")

df = data.load_all()
if df.empty:
    st.info("No reports yet.")
    st.stop()

with st.container(horizontal=True, vertical_alignment="bottom"):
    years = sorted(int(y) for y in df["crop_year"].dropna().unique())
    year = st.selectbox("Crop year", ["All"] + years, key="rev_year")
    crop = st.segmented_control("Crop", ["All"] + data.CROPS, default="All",
                                key="rev_crop") or "All"
    states = st.multiselect("States", sorted(df["state"].dropna().unique()),
                            placeholder="All states", key="rev_states")
    q = st.text_input("Search", placeholder="Any word", key="rev_q")
    needs = st.toggle("Needs review only", key="rev_needs",
                      help="No yield found, no location, or a yield that doesn't fit "
                           "the crop (soybeans over 120 bpa, corn under 80 outside silage).")

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
if needs:
    f = f[f["yield_bpa"].isna() | f["location"].isna()
          | ((f["crop"] == "Soybeans") & (f["yield_bpa"] > 120))
          | ((f["crop"] == "Corn") & (f["yield_bpa"] < 80) & ~f["is_silage"])]

f = f.sort_values(["crop_year", "state", "location"], ascending=[False, True, True],
                  na_position="last").reset_index(drop=True)
hashes = f["dedup_hash"].tolist()
show = f[EDIT_COLS].copy()
show.insert(0, "delete", False)

st.caption(f"{len(show):,} reports. Edit cells and save before changing filters — "
           "unsaved edits are dropped when the list changes.")
edited = st.data_editor(
    show, key="rev_editor", hide_index=True, num_rows="fixed", height=540,
    column_config={
        "delete": st.column_config.CheckboxColumn("Delete", width="small"),
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
edits = {pos: {k: v for k, v in vals.items() if k != "delete"}
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
    data.invalidate()
    st.session_state["rev_flash"] = f"Saved {n} row(s)."
    st.rerun()

if delete:
    n = sum(db.delete_row(h) for h in to_delete)
    data.invalidate()
    st.session_state["rev_flash"] = f"Deleted {n} row(s)."
    st.rerun()
