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
- **Emails, automatically**: the Windows scheduled task **"Yield Portal - email
  sync"** on Kolten's PC runs `pythonw match_emails.py --target snowflake --auto`
  every 30 minutes while he's logged in (Outlook COM needs his session; missed
  runs catch up). `--auto` writes, logs to `logs/email_sync.log` (gitignored —
  it can quote reports) and records each run in `EMAIL_SYNC_RUNS`; Review & edit
  shows when the emails were last checked and flags a failed run. A report with
  no crop or state is stored anyway and waits as **Needs crop/state**
  (`incomplete` check) instead of being lost. Moving this to the droplet needs
  Microsoft Graph mail access from IT (the basis tracker's pending request).
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

## Review checks — nothing counts until it passes or a person clears it

`checks.py` runs **on every load** (inside `data.frame()`), whatever path a report
came in by, and never changes a report — it raises flags:

| flag | rule | suggested fix |
|---|---|---|
| `incomplete` | no crop or no state (an email nobody could place) | — set them by hand |
| `split` | yields for both corn and soybeans in one report (`parse_pdfs.split_by_crop`) | one report per crop; the original is marked `superseded` |
| `range` | corn outside 50–300 bpa, soybeans under 10 | — |
| `corn?` | soybeans over 100 bpa (corn filed under the soybean header) | crop → Corn |
| `reread` | today's parser reads a different yield or LY from the stored text | the fresh reading |
| `duplicate` | same year, crop, state, place (`data.loc_key`) and yield as another row | — |

**One report per crop; one figure per report.** `split_by_crop` follows the crop
each sentence names (or the one before) and suggests a split only when both crops
carry a yield — never automatically, and never in the bulk fix: it reads a bean
report's "Corn last year. 75 bpa two years ago" (rotation history) or a second
report run into the line as the other crop. Several fields of one crop stay one
report (a chatty farm shouldn't count five times): the first figure is the
yield, unless the report states a whole-farm/overall average, which then leads
(`FARM_AVG_RE`, skipped when another place is named in between). The text-only
checks (re-read, split) are computed once with the cached reports
(`checks.row_checks`), so a decision doesn't re-parse 1,300 reports.

A person decides on **Review & edit → Needs review**: apply the fix, approve as it
is, keep both (duplicates), exclude, or edit by hand. Decisions live in
`REVIEW_DECISIONS` (its own table — the service login can create it, and
`YIELD_OBSERVATIONS` never changes shape): `approved` covers the flags it was given
for (a new flag later asks again); `excluded` stays in the archive but out of every
average; `superseded` means replaced by other rows and is hidden everywhere
(`data.load_all()` drops it). Explore counts only `clean` + `approved`; Report text
shows every live report. A hand edit in the table records an approval for
`reread`, so the parser never suggests undoing it. Nothing in the review path
deletes a row.

`python reimport_pdfs.py --target sqlite|snowflake [--apply]` re-reads the PDFs and
splits rows the parser used to glue together (dry run by default): parts are
inserted, carrying the old row's date/email/notes, and the old row is marked
`superseded` with the new hashes in its note. A second run finds nothing. (What
the first run did, and what the checks found, is in `CLAUDE.local.md`.)

## Reports vs normal — the reports against NASS (internal)

`app_pages/normal.py`, registered only off the view link. Each report's yield (its
first figure, `yield_bpa`) is divided by three NASS baselines (`analysis.attach`):
the 5-season average before its season and last season's final — its county's when
the place matches a county, else its state's — and USDA's state number for its own
season (the final, else the latest monthly forecast). Medians; every report counts
once (acres are not used); a confidence label on every number by report count
(`analysis.tier`: under 3 "Too few", 3–9 "Directional", 10+ "Firmer"); thin counties
pulled toward their state, `(n·raw + 3·state)/(n + 3)`, shown raw and pulled. No
trend test: too few seasons. Reports run optimistic (about 1.1–1.2× NASS), so the
page reads each season against the same ratios in earlier seasons, never against
1.0. Plus the reporters' own same-field "vs last year" figures, which need no NASS.

NASS comes **only** from the fleet's cache (`nass.py`), never the live API: one
Snowflake query for the county-yield and state-yield rows, keyed with the vendored
`nass_cache_client._cache_key` (keep that file byte-identical to usda-nass-etl's).
`nass.county_params` / `state_params` must match `jobs/rma_map.py` (first COUNTY
family) and `jobs/domestic_production.py` exactly, or the key misses and the page
goes blank. The current season's NASS "YEAR" row is USDA's latest forecast, not a
final: it counts as final only when loaded after the following January. The
portal's role needs SELECT on `JSA.NASS_CACHE.NASS_CACHE` (granted 2026-10-04).

County matching (`places.py`) works on a key derived from the location; the text
itself never changes. A county named with a county word ("Northern Vermilion
County") or several ("Moultrie/Coles Co") is used straight away; a near-spelling
("Vermillion Co") or a bare town that shares a county's name ("Peoria" — but "Des
Moines" is in Polk County) waits on **Review & edit → Places** (`COUNTY_MATCHES`);
towns and regions use the state's numbers rather than a guessed county.

## Field issues — a reference, not a damage estimate (internal)

`app_pages/field_issues.py` + `field_issues.py`, registered only off the view link.
Reads the `disease` tags (binary, no severity — reporters don't give it; grouped
Disease / Weather / Pests & field) and shows: how often each was noted **with the
denominator** (every report that season), every place it was noted (single mentions
included), places where the same issue recurs across seasons, and yields "observed
alongside" it (median ratio to the 5-season average, noted vs not noted in the same
state, crop and season) **only when both sides have 5+ reports**. Copy rules: "noted",
"mentioned", "observed alongside"; never "caused", "cost" or "loss of". Every view
carries `FI.BLANK_LINE` (voluntary reporting: a blank county is not a clean county);
Explore's disease chart carries it too, with denominators in the table.

The vocabulary is the reports' own (`parse_pdfs.DISEASE_TAGS`), not a textbook list:
drought, excess water and rust dominate. Goss's wilt, anthracnose, phytophthora,
compaction and nutrient deficiency were added so they're caught if they turn up.
Stored tags are the source of truth (editable on Review & edit); a tag-list change
doesn't rewrite stored rows.

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
`data.find_match`** (and `tests/test_checks.py` after touching `checks.py`) —
made-up reports in the shapes the real ones take, plus the real-data file when
present. Rules the cases pin down: a "last year" never
attaches across a full stop or past another number; "above/better than last
year" is a comparison, not last year's figure; "less/more than" is always a
difference; a small number before "better/less than" is a difference; "expected
N" and "thought it was / would be N" make N the expectation, but "better than
expected N" or "than we thought N" make N the yield; "vs N target/budget" is the
expectation; a
"last year" that opens its own clause ("231, fwiw last year ... was 238") belongs
to the next figure; "N bu higher YoY" / "N bu difference" is a change at any size;
a date ("planted 4/12 – 241") or road ("Hwy 30- 66") is never the low end of a
range; "160 A" is acres; same place with a different yield is a different report.
PDF lines opening "Place Co, ST ..." or "Town, ST ..." (comma, no separator) start
a report; "Polk Co – ..." with no state starts one in the previous report's state.

Regex traps already hit: `\w*` backtracking past a negative lookahead (pin with
`\b`); `(?![\d.])` also rejects a sentence-ending full stop (use
`(?!\d)(?!\.\d)`); `r'\\s+'` inside a raw string is a literal backslash; a
lookbehind `(?<![/\d.])` also rejects "vs.73" (use `(?<![/\d])(?<!\d\.)`); a
DataFrame column named `flags` collides with `DataFrame.flags` (hence
`review_flags`).

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

**One Snowflake session per process.** Every `sf_connect` returns a shared,
kept-alive connection (`_KeptOpen`, `close()` is a no-op): a login costs 2-3 s
and a query well under 1 s, so per-call logins made each review click take 6-10
s. Callers take their own cursor. If the session dies, `_retry_once` (on every
db/places/nass call that touches Snowflake) clears it, logs in again and reruns
the call once. The import's temp staging table gets a unique name, because two
imports may share the session. `data.load_all()` is built from two caches —
`_reports()` and `_decisions()` — so a review decision calls
`data.invalidate_decisions()` (re-reads the small decisions table) and only a
changed report calls `data.invalidate()`. Measured 2026-10-04: approve ~1.5 s
(was ~6.5), apply fix ~3.3 s (was ~9.5), first load after a restart ~10 s.

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
