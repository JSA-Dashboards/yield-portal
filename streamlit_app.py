"""
Yield Portal — county yield reports from Ag Trader Talk (archived from the annual
PDFs and the report emails) and JSA's own, with entry and review screens.

Run locally:  streamlit run streamlit_app.py

Access, as in the River FOB portal:
  - ?view=1 is the read-only share link: Explore and Report text, no
    downloads, no password. Add reports, Review & edit and Import PDF don't
    exist there.
  - Everything else sits behind EDIT_PASSWORD.
  - With no EDIT_PASSWORD set (local dev) the app is open.
"""
import hmac
import os
import pathlib
import sys

import streamlit as st

HERE = pathlib.Path(__file__).resolve().parent


def _fresh_modules():
    """Streamlit Cloud pulls each push and re-reads this script and the pages, but
    keeps the modules it already imported (parse_pdfs, checks, db, ...): a pushed fix
    to them never ran until a Reboot. On 2026-10-06 the review queue ran the
    morning's first code all evening, and applying its stale suggestions wrote
    wrong figures. So: whenever one of this app's .py files changes (and once per
    process), forget this app's modules; the imports below load them as pushed."""
    seen = tuple(sorted((p.name, p.stat().st_mtime_ns) for p in HERE.glob("*.py")))
    if getattr(sys, "_yield_portal_code", None) == seen:
        return
    for name, mod in list(sys.modules.items()):
        path = getattr(mod, "__file__", None)
        if name != "__main__" and path and pathlib.Path(path).resolve().parent == HERE:
            sys.modules.pop(name, None)
    st.cache_data.clear()
    sys._yield_portal_code = seen


_fresh_modules()

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
               "EDIT_PASSWORD"):
        if _k in st.secrets and not os.environ.get(_k):
            os.environ[_k] = str(st.secrets[_k])
except Exception:
    pass  # no secrets configured — fine locally

import db  # noqa: E402  (after the env is populated)

st.set_page_config(page_title="Yield reports · JPSI", page_icon=":material/agriculture:",
                   layout="wide")
st.logo("https://www.jpsi.com/wp-content/themes/gate39media/img/logo-full.png",
        link="https://www.jpsi.com", size="large")

VIEW_ONLY = str(st.query_params.get("view", "")).lower() in (
    "1", "true", "yes", "read", "readonly", "view")
st.session_state["view_only"] = VIEW_ONLY


def _require_password():
    """Stop at a password prompt until EDIT_PASSWORD is entered. Not called for
    the ?view=1 link; with no password configured (local dev) the app is open."""
    if st.session_state.get("_authed"):
        return
    expected = (os.environ.get("EDIT_PASSWORD") or "").strip()
    if not expected:
        return
    with st.container(border=True, width=420):
        st.markdown("#### :material/lock: Yield reports")
        st.caption("Enter the password to open the portal.")
        pw = st.text_input("Password", type="password", key="_pw")
        if pw:
            if hmac.compare_digest(pw, expected):
                st.session_state["_authed"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    st.stop()


if not VIEW_ONLY:
    _require_password()
db.init_db()

pages = [st.Page("app_pages/explore.py", title="Explore", icon=":material/insights:",
                 default=True),
         st.Page("app_pages/report_text.py", title="Report text", icon=":material/article:")]
if not VIEW_ONLY:                       # the internal pages never exist on the view link
    pages += [
        # Internal until the numbers have been reviewed (Kolten, 2026-10-04).
        st.Page("app_pages/normal.py", title="Reports vs normal",
                icon=":material/compare_arrows:"),
        st.Page("app_pages/field_issues.py", title="Field issues",
                icon=":material/coronavirus:"),
        # Variety trials are internal for now: Ohio State has not given written
        # permission for derived use of its corn test, and the team agreed this stays
        # out of anything customer-facing until the permissions come back.
        st.Page("app_pages/trials.py", title="Variety trials",
                icon=":material/science:"),
        # ISA states its copyright and no other terms: internal, like the trials above.
        st.Page("app_pages/strip_trials.py", title="Strip trials",
                icon=":material/view_week:"),
        st.Page("app_pages/add.py", title="Add reports", icon=":material/add_circle:"),
        st.Page("app_pages/review.py", title="Review & edit", icon=":material/edit_note:"),
        st.Page("app_pages/import_pdf.py", title="Import PDF",
                icon=":material/upload_file:"),
    ]
page = st.navigation(pages, position="top" if len(pages) > 1 else "hidden")

if not VIEW_ONLY:
    with st.sidebar:
        st.caption(f"Data: {db.backend_name()}")

page.run()
