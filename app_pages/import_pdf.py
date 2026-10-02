"""Import PDF — load an annual yield PDF; only reports not already stored are added."""
import datetime as dt
import re

import pandas as pd
import streamlit as st

import data
import db
import parse_pdfs as P


@st.cache_data(max_entries=4, show_spinner="Reading the PDF…")
def _parse(pdf_bytes: bytes, year: int, name: str):
    return P.parse_pdf(pdf_bytes, year, source_file=name)


st.title("Import PDF")
st.caption("Upload one of the annual Ag Trader Talk yield PDFs. Reports already in the archive "
           "are recognised and skipped, so re-importing an updated PDF only adds what's new.")
if msg := st.session_state.pop("imp_flash", None):
    st.success(msg, icon=":material/check_circle:")

up = st.file_uploader("Annual yield PDF", type="pdf")
if up is None:
    st.stop()

guess = re.search(r"(20\d\d)", up.name)
year = st.number_input("Crop year", min_value=2015, max_value=2100, step=1,
                       value=int(guess.group(1)) if guess else dt.date.today().year,
                       help="Read from the file name when it contains a year.")
rows = _parse(up.getvalue(), int(year), up.name)

stored = db.existing_hashes([r["dedup_hash"] for r in rows])
new, seen = [], set()
for r in rows:
    if r["dedup_hash"] not in stored and r["dedup_hash"] not in seen:
        seen.add(r["dedup_hash"])
        new.append(r)
repeats = len(rows) - len(new) - sum(1 for r in rows if r["dedup_hash"] in stored)

with st.container(horizontal=True):
    st.metric("Reports in the PDF", len(rows), border=True)
    st.metric("Already stored", len(rows) - len(new) - repeats, border=True)
    st.metric("New", len(new), border=True)
    if repeats:
        st.metric("Repeated inside the PDF", repeats, border=True,
                  help="Identical lines that appear more than once in the file — kept once.")

if new:
    preview = pd.DataFrame(new)[["crop", "state", "location", "yield_bpa", "ly_yield",
                                 "aph", "maturity", "irrigation", "disease", "raw_text"]]
    st.dataframe(preview, hide_index=True, height=380, column_config={
        "crop": "Crop", "state": "State", "location": "Location",
        "yield_bpa": st.column_config.NumberColumn("Yield", format="%.1f"),
        "ly_yield": st.column_config.NumberColumn("LY", format="%.1f"),
        "aph": st.column_config.NumberColumn("APH", format="%.0f"),
        "maturity": "Maturity", "irrigation": "Irrigation",
        "disease": "Disease / damage",
        "raw_text": st.column_config.TextColumn("Report", width="large"),
    })
    if st.button(f"Import {len(new)} new report(s)", type="primary",
                 icon=":material/upload:"):
        inserted, _ = db.insert_new(new)
        data.invalidate()
        _parse.clear()
        st.session_state["imp_flash"] = f"Imported {inserted} report(s) from {up.name}."
        st.rerun()
else:
    st.info("Everything in this PDF is already in the archive.")
