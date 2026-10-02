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

Two ways in: the `SNOWFLAKE_*` env (key-pair or password) — what the deployed
app uses — or `SNOWFLAKE_CONNECTION_NAME`, a profile in
`~/.snowflake/connections.toml` for local runs (browser sign-in once per script
run; the connection is shared in-process). Token caching is OFF on that path:
with `keyring` installed the connector writes the OAuth token to Windows
Credential Manager, which rejects it (`CredWrite: The stub received bad data`)
and the connect fails. Don't install keyring for this.

```
python snowflake/setup.py --connection <profile> --check   # prove the sign-in
python snowflake/setup.py --connection <profile>           # create + copy, one connection
```

The copy is from local SQLite (not the PDFs) so dates and edits come across. It
never creates a warehouse unless asked (`--create-warehouse`; billable).

`data.frame()` keeps `date_reported` as plain dates in an object column — with
every value NULL (a fresh table) pandas would make it datetime64 and later date
assignments fail.

## Deployment

Streamlit Community Cloud from this repo (branch `master`, `streamlit_app.py`).
The repo was created directly in the org — a transferred repo's webhook
silently stops deploying. The app is **public** on Community Cloud (one private
app per workspace), so access is by password: `EDIT_PASSWORD` (everything) and
`VIEW_PASSWORD` (read-only Explore, no downloads; optional — without it one
password opens everything); `?view=1` forces read-only but still needs a password.

The app logs in as a **service user** with key-pair auth: `YIELD_PORTAL_SVC`
(TYPE=SERVICE) with `YIELD_PORTAL_ROLE` (usage on the warehouse / YIELD_REPORTS /
PUBLIC; SELECT/INSERT/UPDATE/DELETE on the table; CREATE TABLE on the schema for
the temp staging table imports use) — `snowflake/service_user.sql`. Secrets:
`USE_SNOWFLAKE`, `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_ROLE`,
`SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_PRIVATE_KEY` (PEM in `"""…"""`),
`EDIT_PASSWORD`, `VIEW_PASSWORD`. Never set `SNOWFLAKE_DATABASE`/`_SCHEMA`.
Prove the login path with `python snowflake/verify_service.py` (no browser,
never prints key content).

## Running

`streamlit run streamlit_app.py` from **inside this folder** — Streamlit reads
`.streamlit/` from the CWD, so a parent folder's `secrets.toml` would leak in.
With no passwords set (local) the app is open.

## Charts

Year colours are pinned (`data.YEAR_PALETTE`, newest year = JPSI blue) and
validated with the dataviz skill's validator; aqua/yellow are under 3:1 on white,
so every multi-year chart ships a **Table** view — keep it.
