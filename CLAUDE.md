# Yield Portal

Archive, entry and presentation of Ag Trader Talk's harvest yield-scout
reports: county-level corn and soybean yields, Aug–Nov each year from 2023. One
row per field report in `YIELD_OBSERVATIONS`.

**This repo is PUBLIC.** No report text, data, keys, account identifiers or
personal addresses go in it. Environment specifics and real-data notes live in
`CLAUDE.local.md`, and real-data tests in `tests/test_parsing_local.py` — both
gitignored, on Kolten's PC only. Read `CLAUDE.local.md` when it's there.

## Sources → rows

- **Annual PDFs**: `parse_pdfs.py` → `load_pdfs.py --target sqlite|snowflake`,
  or upload on **Import PDF**.
- **Emails, in bulk**: `python match_emails.py --target ... [--apply]
  [--msg file.msg ...]` reads the report emails from Outlook over COM (dry run
  by default), and per report: stamps the date on the matching archive row,
  adds it as new, or lists it for review. Oldest first, so the first report
  date wins; an email whose Message-ID is on a row is skipped (idempotent). Only
  sees Outlook's cached mail — Outlook must be open and synced.
- **Emails, one at a time**: paste on **Add reports** — same parser and matcher.
- **By hand**: **Add reports → Enter by hand**.
- **Email format traps** (all in `parse_email` / `_email_lines`): the mail
  filter puts the report in `.Body` twice (preview + zero-width padding before
  its banner, real body after) — keep only what follows the banner; `*<tab>`
  bullets; a place alone on a line with "Corn: ..." on the next; two reports
  run into one line; state-less bodies split at county / region / "Corn -"
  openers; `RESEND: Correcting Subject YIELD: ...`; NE/NC/SC before a state is a
  direction ("NE MO" is northeast Missouri).

`dedup_hash` (year | crop | state | location | whitespace-normalised text) is the
row identity, computed once at ingest and never recomputed, so editing a row and
re-importing the same PDF never duplicates it. Exact duplicate lines inside a PDF
collapse to one row.

## Variety trials — the second archive

**Variety trials** shows what the state universities published at each test plot, so a
scout's field report can be read against a replicated one. Two read-only tables,
`TRIAL_SITE_YEARS` (a location in a season) and `TRIAL_STATE_YEARS` (the programme's
average against the USDA state yield and its fitted trend), owned by `trials.py`.

Built elsewhere: `export_portal_tables.py` in the `illinois-corn-trials` project writes
`portal_site_years.csv` and `portal_state_years.csv` from `combine_states.py`, so the
portal can never disagree with the published page about a state's number. Then:

```
python load_trials.py                     # local SQLite
python load_trials.py --target snowflake  # the .env service login
```

Both tables are **dropped and recreated** on load, not merged: a reload follows an
upstream parser change, and the whole point is that a site-year's figure moves when the
extractor improves. A column added upstream therefore needs no ALTER.

**The page is internal.** Ohio State has not given written permission for derived use of
its corn test, and the team's decision is that this stays out of anything customer-facing
until the permissions come back — so it is registered only when `not VIEW_ONLY`, and the
CSVs are **not** committed (this repo is public). Indiana is absent from the data itself:
Purdue permits reproducing its tables only whole and unmanipulated.

`comparable` is false where a programme's locations change so much across its run that a
fitted line tracks composition rather than the season (Nebraska dryland corn runs eastern
counties to 2016 and western ones from 2020, and falls 140 bu without a bad year). Those
programmes are off by default and the page says why.

## Fields are the user's choice — don't widen them

Kept: yield (`yield_bpa` + low/high + `ly_yield` + `expected_yield`), `aph`,
`maturity` (text: corn RM days `108-112`, soy MG `2.6`), `irrigation`
(Irrigated / Non-irrigated / Mixed / NULL = not stated), `disease`
(comma-joined tags; weather damage included). **Dropped on purpose: moisture,
acres.** Never add test weight, fungicide, harvest progress or planting date.
The full original text is always kept in `raw_text`.

## Yield attribution is the fragile part — run the tests

A report line mixes this year's yield, last year's, the expectation, and
differences. `extract_yields` classifies every number. **`python
tests/test_parsing.py` after any change to `parse_pdfs.py` or
`data.find_match`** — made-up reports in the shapes the real ones take, plus the
real-data file when present. Rules the cases pin down: a "last year" never
attaches across a full stop or past another number; "above/better than last
year" is a comparison, not last year's figure; "less/more than" is always a
difference; a small number before "better/less than" is a difference; "expected
N" makes N the expectation; same place with a different yield is a different
report.

Regex traps already hit: `\w*` backtracking past a negative lookahead (pin with
`\b`); `(?![\d.])` also rejects a sentence-ending full stop (use
`(?!\d)(?!\.\d)`); `r'\\s+'` inside a raw string is a literal backslash.

## Backend

`db.py`: Snowflake when `USE_SNOWFLAKE` is truthy, else local SQLite
(`yield_portal.db`, gitignored). The Snowflake database is **pinned in code**
(`YIELD_REPORTS.PUBLIC`, override with `YIELD_DATABASE`/`YIELD_SCHEMA`) — it
never reads `SNOWFLAKE_DATABASE`/`SNOWFLAKE_SCHEMA`, so a multi-app host's
globals can't redirect it.

**Never name a folder in this repo `snowflake`** — the provisioning scripts live in
`snowflake_admin/` for this reason, renamed 2026-10-02.

A folder named `snowflake` beside the app makes `snowflake` a namespace package whose
`__path__` includes it, so Streamlit's watcher counts it as a *local* module — and on
every file change the watcher deletes all watched modules from `sys.modules`
(`local_sources_watcher.py`: "we simply unload all watched modules"). Pages then fail
mid-session with *module 'snowflake' has no attribute 'connector'* on a server that
started fine, which cost two sessions an afternoon. `server.folderWatchBlacklist` cannot
help: its globs match a path's *parent* folder, so nothing short of blacklisting the whole
app folder reaches it.

`sf_connect` still reaches the connector with
`importlib.import_module("snowflake.connector")` rather than `import snowflake.connector
as sc` — it returns the submodule from `sys.modules` instead of reading it off the parent,
which survives that deletion. Keep both defences; `db.py` is the only place in the repo
that imports the connector.

Two ways in: the `SNOWFLAKE_*` env (key-pair or password) — what the deployed
app uses — or `SNOWFLAKE_CONNECTION_NAME`, a profile in
`~/.snowflake/connections.toml` for local runs (browser sign-in once per script
run; the connection is shared in-process). Token caching is OFF on that path:
with `keyring` installed the connector writes the OAuth token to Windows
Credential Manager, which rejects it (`CredWrite: The stub received bad data`)
and the connect fails. Don't install keyring for this.

```
python snowflake_admin/setup.py --connection <profile> --check  # prove the sign-in
python snowflake_admin/setup.py --connection <profile>          # create + copy, one connection
```

The copy is from local SQLite (not the PDFs) so dates and edits come across. It
never creates a warehouse unless asked (`--create-warehouse`; billable).

`data.frame()` keeps `date_reported` as plain dates in an object column — with
every value NULL (a fresh table) pandas would make it datetime64 and later date
assignments fail.

## Deployment

Streamlit Community Cloud from this repo (branch `master`, `streamlit_app.py`).
The repo was created directly in the org — a transferred repo's webhook
silently stops deploying. The app is **public** on Community Cloud (one private app per workspace), so
access works like the River FOB portal: `?view=1` is the password-free
read-only share link — Explore and Report text, no downloads, and Add reports /
Review & edit / Import PDF aren't even registered there ("those are just for us").
Everything else needs `EDIT_PASSWORD`. On the view link a database error shows
a plain message, never connection details.

The app logs in as a **service user** with key-pair auth: `YIELD_PORTAL_SVC`
(TYPE=SERVICE) with `YIELD_PORTAL_ROLE` (usage on the warehouse / YIELD_REPORTS /
PUBLIC; SELECT/INSERT/UPDATE/DELETE on the table; CREATE TABLE on the schema for
the temp staging table imports use) — `snowflake_admin/service_user.sql`. Secrets:
`USE_SNOWFLAKE`, `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_ROLE`,
`SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_PRIVATE_KEY` (PEM in `"""…"""`),
`EDIT_PASSWORD`. Never set `SNOWFLAKE_DATABASE`/`_SCHEMA`. After changing
Secrets, **Reboot** the app: the secrets→env bridge in `streamlit_app.py` never
overwrites a variable that's already set, so a running app keeps the old value.
Prove the login path with `python snowflake_admin/verify_service.py` (no browser,
never prints key content).

## Running

`streamlit run streamlit_app.py` from **inside this folder** — Streamlit reads
`.streamlit/` from the CWD, so a parent folder's `secrets.toml` would leak in.
With no passwords set (local) the app is open.

## Report text

**Report text** (`app_pages/report_text.py`, logic in `report_text.py`) lays the
reports out the way the annual PDF does — crop, state, then each report in its own
words with its report date — from the same `raw_text` (nothing extra is stored).
Within a state: the PDF's order (report date, undated after by place) or by place.
Word (python-docx) and PDF (PyMuPDF's Story HTML layout) downloads are built on
click from what's on screen, and are hidden on the view link like the CSV.

Report text is free text, so it is escaped for Markdown (`~10%` would strike
through, `$395 … $255` would turn into math) and for HTML. Each state is a
Markdown table, not `st.table`: `st.table` caps a Markdown cell at 400px, which
squeezes paragraphs into a narrow column. Run `python tests/test_report_text.py`
after changes.

## Charts

Year colours are pinned (`data.YEAR_PALETTE`, newest year = JPSI blue) and
validated with the dataviz skill's validator; aqua/yellow are under 3:1 on white,
so every multi-year chart ships a **Table** view — keep it.
