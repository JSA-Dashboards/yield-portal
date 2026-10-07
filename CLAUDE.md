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
  sees classic Outlook's local cache, which is only as fresh as its last sync
  (Kolten reads mail in the new Outlook). The Archive folder syncs after the
  Inbox and can lag months behind the server. Mail the cache can't reach yet:
  save it from Outlook's server search as `.msg` and pass it with `--msg`.
  Skipped: emails without YIELD in the subject (a conversation with the source
  carries signatures, not reports). A colleague's forward ("FW: YIELD: ...") is
  read as the original: subject, report and send time come from the innermost
  forwarded header block (`parse_pdfs.unwrap_forward`).
- **Emails, automatically**: the Windows scheduled task **"Yield Portal - email
  sync"** on Kolten's PC runs `pythonw match_emails.py --target snowflake --auto`
  every 30 minutes while he's logged in (Outlook COM needs his session; missed
  runs catch up). `--auto` writes, logs to `logs/email_sync.log` (gitignored —
  it can quote reports) and records each run in `EMAIL_SYNC_RUNS`; Review & edit
  shows when the emails were last checked and flags a failed run. A report with
  no crop or state is stored anyway and waits as **Needs crop/state**
  (`incomplete` check) instead of being lost. When the task has to start Outlook
  itself (no window), it waits up to 4 minutes for the cache to settle before
  reading.
- **Emails from the server (the Droplet path)**: `--source graph` reads the
  whole mailbox through Microsoft Graph instead (`graph_mail.py`: app-only
  OAuth, every folder, HTML body → Outlook-style text). The saved emails parse
  identically both ways (local test). `deploy/run_email_sync.sh` +
  `deploy/DROPLET.md` hold the Droplet setup. **Blocked on IT**: the tenant's
  only Graph app has Mail.Send, not Mail.Read (checked 2026-10-04), and
  `graph_mail` says so instead of a bare 403. Until then the PC task stays.
- **Emails, one at a time**: paste on **Add reports** — same parser and matcher.
- **By hand**: **Add reports → Enter by hand**.
- **JSA's own reports**: not every report is Ag Trader Talk's. Add reports has a
  **Source** switch for both modes; JSA's are stored as `report_source = "jsa"`
  (`pdf` / `email` / `manual` are Ag Trader Talk's) and shown as the `source`
  column (`data.SOURCE_LABEL`). JSA sends some of its reports to Ag Trader Talk,
  who can email them back out: the matcher dates the stored row rather than adding
  it again (a pasted JSA report that matches an Ag Trader Talk row adds a "JSA
  reported this too" note), and the `duplicate` check catches a repeat it misses;
  the review queue shows each copy's source.
- **Seed-company plots**: only plot results customers share with JSA. Pioneer's
  terms forbid even manual scraping, and Beck's and AgriGold reserve all rights,
  so their websites are never collected from. The Add reports **Seed plot**
  source takes a plot's whole history as a table, one row per season (company,
  place, plot average across entries, entries, notes), stored as
  `report_source = "plot"` with the company in `source_file`. Plots are their own
  source (`data.HEADLINE_SOURCES` leaves them out). They're off by default on
  Explore, kept out of the Reports vs normal headline and out of the weekly
  email's tiles and charts, and tagged "<company> plot" in Report text and the
  email. **Read against their own history** (`plots.py`): each plot season against
  the straight-line trend of that plot's earlier seasons (5+ needed) and the
  same plot last season. The NASS comparison is shown only for a plot with too
  little history (Kolten: "regress its own history").
- **Email format traps** (all in `parse_email` / `_email_lines`): the mail
  filter puts the report in `.Body` twice (preview + zero-width padding before
  its banner, real body after) — keep only what follows the banner; `*<tab>`
  bullets; a place alone on a line with "Corn: ..." on the next; two reports
  run into one line; state-less bodies split at county / region / "Corn -"
  openers; `RESEND: Correcting Subject YIELD: ...` and `CORRECTION: YIELD: ...`;
  NE/NC/SC before a state is a direction ("NE MO" is northeast Missouri); state
  headings ("Missouri:") over bullets that name no state, kept as state headers;
  "-Linn Co-Central MO- 100 acres" bullets (dash with no space, hyphens for
  commas, region before the state: `HYPHEN_CO_RE`) and "Greene County-picked"
  (`NOSTATE_CO_RE` takes a hyphen running straight into the text, not "Co-op").
- **No crop named anywhere** (not in the line, a header or the subject): over
  `P.SOY_MAX` (100 bpa) the report is corn, filed with a note saying the crop
  came from the yield. At 100 or under either crop is possible, so it's stored
  crop-less and waits on Review & edit (Kolten: "apply the yield range logic …
  or at minimum ask me").

`dedup_hash` (year | crop | state | location | whitespace-normalised text) is the
row identity, computed once at ingest and never recomputed, so editing a row and
re-importing the same PDF never duplicates it. Exact duplicate lines inside a PDF
collapse to one row.

## Review checks — nothing counts until it passes or a person clears it

`checks.py` runs **on every load** (inside `data.frame()`), whatever path a report
came in by, and never changes a report — it raises flags:

| flag | rule | suggested fix |
|---|---|---|
| `incomplete` | no crop or no state (an email nobody could place) | crop → Corn when the state is known and the yield is over 100 bpa (new emails get that at parse time); else set by hand |
| `split` | yields for both corn and soybeans in one report (`parse_pdfs.split_by_crop`) | one report per crop; the original is marked `superseded` |
| `entries` | yields for several fields or both practices (`split_entries`) in a report that hasn't become that many live rows yet | one report per entry (`checks.entry_rows`); a stored row that already is an entry is kept, the report's other rows `superseded`; in the bulk fix |
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
checks (re-read, split, entries) are computed once with the cached reports
(`checks.row_checks`), so a decision doesn't re-parse 1,300 reports.

**Every entry is a report** (Kolten, 2026-10-05/06: dryland and irrigated "need to be
separated and both included"; "this had multiple entries, we need to add each one").
About half the reports are keyed in by JSA, half come from Garrett; both carry these.
`split_entries` cuts a report wherever an acreage ("40 acres", "40 ac at", "270a",
"160 A", "600 custom acres") or a practice ("dryland", "non-irrigated", "irrigated";
"non- irrigated" wrapped by a PDF; never "pivot", which can mark dryland corners) is
named, and (Kolten, 2026-10-07, fields without acres: "make this each an entry")
wherever a field is named another way: a designator ("1st field", "Second field",
"One quarter", "another farm", "Other fields", "next field", "Different producer",
"same farmer", "Field #2", "1 field"; not "same field", a comparison, nor "same farm":
"the same farm I reported beans from"), an ordinal with its figure ("another 40",
"2nd 61", "third running about 58"), a numbered or lettered item ("1.)", "2)", "A.)"),
ground named at a clause start ("Sandy ground made 200 bpa. Good ground made 240"),
a corn maturity with its own yield ("105 day corn went 220 dry, 112 day went 238";
not "the 108 day I planted was 240"), and each figure of a list: figures with only
commas, "and", crop words, a field's APH or prior year, or "on two fields" between
("4 fields made 215 bpa, 228 bpa, 236 bpa", "200 bpa corn 228 bpa corn", "176 bpa corn,
aph is 210 188 bpa corn", "205 bpa vs 150 LY 228 bpa vs 230 LY"; a list naming its unit
once, "Field avg 70, 74, 77 bpa", gets it on each figure first, `_unitize`). Not a list:
"Standing corn 235, downed corn 160" (parts of one field), a closing average ("58 vs 66
last year and 61 bpa avg"). A figure before the first cut is the first field's when a
designator that continues one follows (another, other, second, next, different, same
farmer: "52 bpa 58 aph, same farmer 61 bpa"), unless the lead calls it an average
("running 250 bpa avg ... one field 260, another field 270" stays one report). A
report saying a field "pulled the yield down" stays whole: that field is part of the
average. 98 stored reports split this way on 2026-10-07 (248 rows). A piece
with no figure goes with a neighbour: naming the practice of the entry before it, it's
that entry's remark ("... and the non-irrigated average last year"); anything
else opens the next entry; the last closes the last. An entry's practice is the one it
names, else the one before. **No split** when a figure comes before the first cut,
when a whole-farm/overall average is stated (it speaks for the report, `FARM_AVG_RE`),
when another place is named among the entries (a county word, a state abbreviation or
a state in full: PDF lines that run several reports together; also a state in full
after the report's own place, before the entries: "Town, MN ... NE Iowa beans 100
acres 50 bushel", left whole; a region of one state is one place: "NC Nebraska
beans 120 acres ..." splits), when "last year" opens the clause an acreage is in
("last year our 100 acre field made 80" is LY; but "220 bpa vs 190 bpa last year 150
acres made 230" is the next field, `_ly_field`), or when the report also gives both
crops (the crop split goes first, then each half can split). Giving a report's
crop-less opening sentences to the crop it's filed under (so "40 acres made 230 bpa
... Early beans running 70 bpa" splits by crop) was tried and dropped: it split 3 of
4 stored reports wrongly (a bean report saying its field was corn last year).

The rows keep the **same full text**; each reads its own figures from its entry
(`entry_metrics`: APH and maturity fall back to the lead, silage is the lead's or the
entry's, disease the whole report's). Each hash adds its part (`dedup_hash(...,
part)`, carried as `_part` before insert, never stored): a practice with one entry is
just the practice ("Irrigated", as the 2026-10-05 practice split hashed it), several
are numbered ("Non-irrigated|2", "entry|1"). `reread` reads a row as its own entry
(by hash, `checks.entry_hash`: two fields of one report can share a yield), else as
the entry with its practice and yield (a row of a text the parser no longer splits,
split by an older rule or by hand, gets no re-read: its whole-report reading isn't
its own); the duplicate check never pairs rows of one text, nor different practices,
nor fields that name different acres (`checks.field_acres`: "40a 52bpa" in one report
and "80 acres went 52" in another aren't one field reported twice);
`report_text.ordered` shows the shared words once. Every row carries the whole text,
so `checks.run` adds **`field`** ("3 of 4: 40a 52bpa vs. 58bpa in 2024.",
`checks.field_of`), shown as the Field column on Review & edit (queue and all
reports) and Explore, and the queue's detail lists the report's other fields:
without it a split row in the queue looked like one yield taken for a whole
five-field report (Kolten, 2026-10-06). The fix keeps a stored
row that already is an entry and gives it its entry's figures when the yields agree
(`checks.entry_changes`, the suggestion's `updates`: an irrigated half split on
2026-10-05 had the report's first APH, not its own). Entry hashes use None for a blank field,
as the parser does (`dedup_hash` puts "None" in the key; pandas has NaN). A split
supersedes the report's row, and with it any decision a person made on it: the new
entries can raise the same flag again (a "kept: not the same report" duplicate, an
approved range). Carry those decisions to the entries (the 2026-10-06 batch carried
3; REVIEW_DECISIONS keeps one row per report, Time Travel shows the one before). New reports split at
parse time (`by_entry`, in `parse_lines` and `parse_email`); on **Add reports → Enter
by hand**, leaving Yield and Irrigation empty saves one report per entry, keyed-in
fields kept. 78 PDF reports split this way (1,271 rows → 1,394).

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
`nass.county_params` / `state_params` must match `jobs/yield_portal.py` (county
yields, 2005 on, weekly) and `jobs/domestic_production.py` exactly, or the key misses
and the page goes blank; `nass.FIRST_YEAR` and the job list's `FIRST_YEAR` move
together. Snowflake flattens the payloads to the fields used (`nass._fetch`):
whole payloads are 1-2 MB a county year. The current season's NASS "YEAR" row is USDA's latest forecast, not a
final: it counts as final only when loaded after the following January. The
portal's role needs SELECT on `JSA.NASS_CACHE.NASS_CACHE` (granted 2026-10-04).

**Where NASS skipped the county** (spec 01b, `baselines.py`): NASS publishes
fewer counties each year, so a matched county can lack the figure. The chain is
the county's own figure, then its own straight-line trend for a one-year gap
(`last season` and same-season finals only; 5+ finals with one next to the gap),
then the counties around it, weighted by 1/distance between centre points. The
bordering ring comes first, widening to the second and third rings while fewer
than 3 have the figure; at least 2 are needed. Last comes the state, tagged
`state fallback` and kept out of the headline medians (`analysis.summarize`). A
town or region is tagged `state`, and is counted. Each baseline's source is in
`avg5_level` / `ly_level` / `final_level`; the neighbor FIPS used aren't stored.
In 2026, 64 of 164 reports moved from the state's 5-season average to their
neighbors'. Geography comes from `COUNTY_GEO` (`geo.py`; Census 2023 gazetteer
centre points + county adjacency, public domain, loaded by
`python load_geo.py [--target snowflake]`). Without that table the neighbor step
is skipped. Not done: matching the report's irrigation to NASS's irrigated or
non-irrigated county yields.

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

## Strip trials — ISA's on-farm trials (internal)

**Strip trials** puts the Iowa Soybean Association's replicated on-farm strip trials
(about 4,800 since 2005, all but one in Iowa) beside NASS: each trial is one real field,
so its yield is read against its county's NASS yield (cached from 2005: every trial
through 2014 has one, about 95% since, as NASS stopped publishing some counties) and
Iowa's. One table, `ISA_STRIP_TRIALS`, owned by `isa_trials.py`, dropped and rebuilt on
load like the trial tables.

```
python load_isa.py fetch                       # the trial list + each report's text -> data/isa/
python load_isa.py load [--target snowflake]   # read the text, replace the table
```

`fetch` takes ISA's public database one year at a time (all years at once gives an HTTP
500), then each trial's PDF report with a 2 s pause, keeping only its text
(`data/isa/text/<trial id>.txt`) and skipping what's cached. The first run takes about 3 h,
so it runs on the Droplet. `data/` is gitignored: the repo is public. **Case trap:** the list
links `ST2006128A_Final_Report.pdf` where ISA's case-sensitive server has `ST2006128a_…`
(a 404), so `_report` retries with the suffix letter's case swapped. A 404 is never retried
as-is.

The yields are only in the reports, which have changed layout many times
(`isa_trials.LAYOUTS`, named by the first season seen; labels tolerate the stray spaces
some fonts put inside words, values may carry significance letters). **ISA's listed
response is the check**: a reading counts when some pair of its treatments is that far
apart (0.1 bu, or 1 bu for whole-bushel figures). A layout that is the treatment table
itself (`whole`: 2023 grouped, 2021, lettered 2018L, 2012w) is read entire, so a field's
level is the mean of every treatment even when the list names two of five. Other layouts
try the list's count, then the run's own (the list miscounts both ways). Traps hit:
taking 3 of a 4-treatment run; the 2010 layout's last number is ALWAYS the difference
(773 of 773), and for corn it can look like a yield (48.2). For three or more treatments
ISA's response isn't one defined pair (sometimes the LSD), so an exact-count clean run is
taken as `unverified`. Nothing fitting → `unread`, never a guess.

**One field, several entries:** ISA lists some fields under two or more comparisons (`…A`
and `…A1` share one report; `…0035a` has no report of its own). `field_id` = md5 of the
report text; `isa_trials.fields()` keeps one row per field (the most treatments), and the
page's chart and figures use it. **Crop:** ISA's list mislabels some trials (2017-18 cover
crop trials "corn" at ~60 bu), and some reports' rotation lines are wrong the other way,
so where they disagree the yields decide (`settle_crop`: under 100 bu = soybeans);
`listed_crop` keeps ISA's. Statuses: `read`, `unverified`, `unread`, `same field`, `no
report`. As loaded 2026-10-05: 4,680 of 4,749 reports read (4,600 fields), 69 unread, 3
missing. **`python tests/test_isa_trials.py` after any change**: made-up fragments, one per
layout; then re-run every cached report and diff against the previous readings.

**Internal**: ISA states its copyright and no other terms. The page is registered only
when `not VIEW_ONLY`, and the report text never goes in the repo.

**Also in the admin portal** as the **JSA Yield Observations** tile under Supply & Demand
(`JSA-Dashboards/jsa-admin-portal`, `apps/strip_trials/`, since 2026-10-05): the same
page, read-only, over the same table. Its
`isa_strip_data.py` copies `with_nass`, `fields`, `by_season` and the NASS read from
here, so a change to how fields are counted or compared with NASS belongs in both. It
reads as `ADMIN_PORTAL_ROLE`, which needs SELECT on `YIELD_REPORTS.PUBLIC` (all + future
tables, since this loader drops and recreates the table).

## Fields are the user's choice — don't widen them

Kept: yield (`yield_bpa` + low/high + `ly_yield` + `expected_yield`), `aph`,
`maturity` (text: corn RM days `108-112`, soy MG `2.6`), `irrigation`
(Irrigated / Non-irrigated / Mixed / NULL = not stated), `disease`
(comma-joined tags; weather damage included). **Dropped on purpose: moisture,
acres.** Never add test weight, fungicide, harvest progress or planting date.
The full original text is always kept in `raw_text`.

**`ly_yield` is the prior year, of a like field** (Kolten, 2026-10-07: "people
typically rotate so its prior year of corn or beans. You skip a year due to
rotation. So maybe call it prior year. Also we need to drop the idea of 'same
field' and call it a like field"). It's what the reporter compares with: the last
season a like field grew this crop, often two back ("vs. 62bpa in 2023" in a 2025
bean report; "vs 270 bpa in 2024" in a 2026 corn one). Keep those; the parser
takes "vs N in <any year>" and "last year" alike. Every label says **Prior
year** / "Prior yr" / "vs prior yr" and **like field**, never "last year", "LY"
or "same field" (Explore tile "Vs like field, prior year", the weekly email tile,
Reports vs normal, the Add and edit forms, `describe_fix`). Not renamed: NASS's
county "last season" and a seed plot's "Same plot LY", which really are the
season before at the same place. The column keeps its name, `ly_yield`.

## Yield attribution is the fragile part — run the tests

A report line mixes this year's yield, last year's, the expectation, and
differences. `extract_yields` classifies every number. **`python
tests/test_parsing.py` after any change to `parse_pdfs.py` or
`data.find_match`** (and `tests/test_checks.py` after touching `checks.py`) —
made-up reports in the shapes the real ones take, plus the real-data file when
present. Rules the cases pin down: a "last year" never
attaches across a full stop or past another number; "above/better than last
year" is a comparison, not last year's figure; "less/more than" is always a
difference; a small number before "better/less than" or "behind/ahead (of) last
year" is a difference ("10bpa behind last year" is no LY of 10); "expected
N" and "thought it was / would be N" make N the expectation, but "better than
expected N" or "than we thought N" make N the yield; "vs N target/budget" is the
expectation; a
"last year" that opens its own clause ("231, fwiw last year ... was 238") belongs
to the next figure; "N bu higher YoY" / "N bu difference" is a change at any size;
a date ("planted 4/12 – 241") or road ("Hwy 30- 66") is never the low end of a
range; "160 A" is acres; same place with a different yield is a different report.
The APH is never a yield, nor part of the range, before or after its figure ("APH
was 45 bpa", "vs 60 bpa APH", "vs. 58.5bpa APH"); a figure before "APH" is the APH
unless "APH" names its own ("90 acres 228 bpa aph 205" is APH 205; "(205 APH) 2. 150
acres" is still 205). A yield check is the expectation ("vs a mid-Aug yield check at
215", "field checked in July at 230bpa"). Before 2026-10-06 the range took the APH
("200 bpa vs 185 bpa APH" read as 185–200) and a field APH stated after the yield could
be read as the yield; stored ranges weren't rewritten, `reread` only flags a changed
yield or last year.
What isn't this season's yield, besides the above: a yardstick ("normal is 210",
"Ten-year avg is 65", "typically run 60-65", "225bpa 10-year average", "45 bpa
trendline"), a record on the books ("the record for that field is 72", "my record yield
of 83", "previous record was 225"; "a record 268" is this year's), a change written with
a sign ("-15bpa from last season"), a hope ("Hopes the better ground will be 210",
"estimates should average 68", "would have been 72"; but "total average will be 58" is
the result), a thousands group ("38,250 bushels"). Read as yields: "62½ BPA", "2nd 61",
"field #3 41.5", "second field 52". The prior year of a like field includes "two years
ago" and "Same field in 2023 made 76" (right before the figure only: "in 2023 Early
beans running 70" isn't); "vs 205 two years ago, 60 ac at 118" closes the comparison
with 205, so 118 stays this year's (`_CLOSES_COMPARISON`).
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

Streamlit Community Cloud from this repo (branch `master`, `streamlit_app.py`),
app `yield-app-jsa.streamlit.app` in the **jsa-dashboards** workspace. Cloud pulls
every push ("Pulling code changes … Updated app!") and re-reads the script and the
pages, but **keeps the modules it already imported**: on 2026-10-06 the review queue
ran the morning's first `checks`/`parse_pdfs` all evening, two pushes later, and
applying its stale suggestions overwrote 8 split rows' figures (restored). So
`streamlit_app._fresh_modules` forgets this app's modules whenever one of its `.py`
files changes (and once per process); the next import loads them as pushed.
Charts get only the columns they plot: the data frame's dict/list columns break
Arrow (`data.load_all` also drops the row checks' `_…` working columns).
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

## Weekly email

`weekly_email.py` builds the Tuesday-morning email for the JSA group: for the
current crop year, per crop, Explore's headline tiles (`data.headline`, with %
changes) and two charts (yields by crop year; average by state, this year vs last;
Altair → PNG with `vl-convert-python`, embedded as `cid:` attachments). A yellow
"New this week" box at the top lists the reports dated in the past week, and
the season's report text follows (laid out like Report text), with those reports
highlighted in yellow. "The past week" is the seven days before the send day
(Tue–Mon), so each report date lands in exactly one email. Tiles and charts count
what Explore counts; the text holds everything but superseded / excluded rows.

- `python weekly_email.py --preview [--today YYYY-MM-DD]` writes
  `logs/weekly_preview.html` (images inline) and sends nothing.
- `python weekly_email.py --to <address> [--via graph]` sends now (a test).
- **Runs on the Droplet** (`deploy/run_weekly_email.sh`, Tuesdays 7:00 CT,
  through cron-alert; `deploy/DROPLET.md`): `--scheduled --via graph` sends
  through Microsoft Graph as the basis tracker's shared mailbox (that app has
  Mail.Send), shown as "JSA Yield Reports", with replies to
  `WEEKLY_EMAIL_REPLY_TO`. The Graph settings come from the basis tracker's
  `.env` through `GRAPH_ENV_FILE`, so there's one secret to rotate. The
  recipient is `WEEKLY_EMAIL_TO`, never in this public repo.
- `--scheduled` sends at most once per ISO week, checked against the
  `WEEKLY_EMAILS` table, so a second scheduler left on (the PC's old
  "Yield Portal - weekly email" task, now disabled) can't repeat it.
- `--via outlook` (the PC) sends through classic Outlook over COM. `.Send()` only
  queues it: in cached Exchange mode Outlook sends on its own send/receive
  cycle, so the script waits up to 35 minutes for the Outbox to drain (Morning
  Wire's lesson) and exits 2 if it's still queued.
- `YIELD_PORTAL_URL` in `.env`, if set, adds a link to the portal in the footer.

## Charts

Year colours are pinned (`data.YEAR_PALETTE`, newest year = JPSI blue) and
validated with the dataviz skill's validator; aqua/yellow are under 3:1 on white,
so every multi-year chart ships a **Table** view — keep it.
