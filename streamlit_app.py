"""
Yield Portal — Ag Trader Talk county yield reports, archived
from the annual PDFs and the report emails, with entry and review screens.

Run locally:  streamlit run streamlit_app.py

Access: the app is public on Community Cloud, so everything sits behind a
password. EDIT_PASSWORD opens the whole app; VIEW_PASSWORD opens only the
read-only Explore page (no downloads). ?view=1 forces read-only even for the
edit password. With neither set (local dev) the app is open.
"""
import hmac
import os
import pathlib

import streamlit as st

HERE = pathlib.Path(__file__).resolve().parent

# Locally, settings come from this project's .env; on Streamlit Cloud there is
# no .env and they come from st.secrets (bridged below).
try:
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env")
except ModuleNotFoundError:
    pass

try:
    for _k in ("USE_SNOWFLAKE", "SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER",
               "SNOWFLAKE_PASSWORD", "SNOWFLAKE_ROLE", "SNOWFLAKE_WAREHOUSE",
               "SNOWFLAKE_PRIVATE_KEY", "SNOWFLAKE_PRIVATE_KEY_PATH",
               "SNOWFLAKE_PRIVATE_KEY_PWD", "YIELD_DATABASE", "YIELD_SCHEMA",
               "EDIT_PASSWORD", "VIEW_PASSWORD"):
        if _k in st.secrets and not os.environ.get(_k):
            os.environ[_k] = str(st.secrets[_k])
except Exception:
    pass  # no secrets configured — fine locally

import db  # noqa: E402  (after the env is populated)

st.set_page_config(page_title="Yield reports · JPSI", page_icon=":material/agriculture:",
                   layout="wide")
st.logo("https://www.jpsi.com/wp-content/themes/gate39media/img/logo-full.png",
        link="https://www.jpsi.com", size="large")

VIEW_LINK = str(st.query_params.get("view", "")).lower() in (
    "1", "true", "yes", "read", "readonly", "view")


def _require_password():
    """-> "edit" or "view". Stops the script at a password prompt until one of
    the configured passwords is entered (see the module docstring)."""
    if st.session_state.get("_role"):
        return st.session_state["_role"]
    edit_pw = (os.environ.get("EDIT_PASSWORD") or "").strip()
    view_pw = (os.environ.get("VIEW_PASSWORD") or "").strip()
    if not edit_pw and not view_pw:
        return "edit"                     # nothing configured: local dev
    with st.container(border=True, width=420):
        st.markdown("#### :material/lock: Yield reports")
        st.caption("Enter your password. The view password opens the reports read-only; "
                   "the edit password opens everything.")
        pw = st.text_input("Password", type="password", key="_pw")
        if pw:
            if edit_pw and hmac.compare_digest(pw, edit_pw):
                st.session_state["_role"] = "edit"
                st.rerun()
            elif view_pw and hmac.compare_digest(pw, view_pw):
                st.session_state["_role"] = "view"
                st.rerun()
            else:
                st.error("Incorrect password.")
    st.stop()


ROLE = _require_password()          # always first: ?view=1 is no way around it
VIEW_ONLY = VIEW_LINK or ROLE == "view"
st.session_state["view_only"] = VIEW_ONLY
db.init_db()

pages = [st.Page("app_pages/explore.py", title="Explore", icon=":material/insights:",
                 default=True)]
if not VIEW_ONLY:
    pages += [
        st.Page("app_pages/add.py", title="Add reports", icon=":material/add_circle:"),
        st.Page("app_pages/review.py", title="Review & edit", icon=":material/edit_note:"),
        st.Page("app_pages/import_pdf.py", title="Import PDF",
                icon=":material/upload_file:"),
    ]
page = st.navigation(pages, position="top")

with st.sidebar:
    st.caption(f"Data: {db.backend_name()}")

page.run()
