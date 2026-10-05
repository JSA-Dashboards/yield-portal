"""
Iowa Soybean Association replicated on-farm strip trials: each trial's
treatment yields, read from the text of its report (load_isa.py fetches it), so
a trial field's yield can sit beside its county's NASS yield.

The reports changed layout several times between 2005 and 2025. `parse` tries
each layout's summary of treatment averages in turn and takes as many figures as
the trial has treatments, all in the crop's plausible range. Each reading is
checked against the average response ISA's trial list gives: some pair of
treatments must be that far apart (to the rounding of the figures; for two
treatments, the only pair). The list's treatment count can be wrong for the
report (it says "A vs B" where the report has five nitrogen rates, or "A vs B vs
C" where it compared two), so the summary's own count is tried as well.

For three or more treatments ISA's "response" isn't one defined thing (the
highest less the lowest, the pair the list names, once the LSD). So a summary
of three or more whose count matches the list exactly, with nothing else in the
run, is taken even when no pair agrees, as 'unverified'. A report no reading
fits is kept as 'unread', not guessed at. The trial's yield is the mean of its
treatment averages: treatments mostly move yield a few bushels, and the field's
level is what's compared with NASS. Where the report's rotation line names the
other crop than ISA's list, the yields settle which is right (settle_crop).

ISA states no terms beyond its copyright: the table stays internal (the Variety
trials page, never on the view link) and out of the public repo.
"""
import csv
import hashlib
import os
import re

import pandas as pd
import streamlit as st

import db
import places

TABLE = "ISA_STRIP_TRIALS"
COLUMNS = [("trial_id", "VARCHAR(24)"), ("year", "INTEGER"), ("crop", "VARCHAR(16)"),
           ("county", "VARCHAR(40)"), ("landform", "VARCHAR(60)"), ("district", "VARCHAR(40)"),
           ("trial_type", "VARCHAR(100)"), ("trial_detail", "VARCHAR(300)"),
           ("avg_response", "FLOAT"), ("treatments", "INTEGER"), ("yields", "VARCHAR(160)"),
           ("trial_yield", "FLOAT"), ("layout", "VARCHAR(12)"), ("status", "VARCHAR(12)"),
           ("report_url", "VARCHAR(200)"), ("field_id", "VARCHAR(12)"),
           ("listed_crop", "VARCHAR(16)")]          # ISA's list's crop, where the report's differs
CROP = {"Corn": "Corn", "Soybean": "Soybeans", "Soybeans": "Soybeans"}
RANGE = {"Corn": (40.0, 350.0), "Soybeans": (10.0, 120.0)}
N = r"-?\d{1,3}(?:\.\d+)?"                  # one number token
G = r"(?: +[a-h]{1,4})?"                    # its significance letters, if any ("212.8 ab")
RUN = rf"((?:{N}{G} \| ){{2,9}})"            # a run of number tokens, ' | '-separated
LETTERED = rf"((?:{N} +[a-h]{{1,4}} \| ){{2,9}})"  # a run where every number has its letters
CELL = r"(?: \| | ?)"                        # a label's words in one cell or split over several
MAX_TREATMENTS = 6


def L(label: str) -> str:
    """A label as a regex that tolerates the stray spaces some reports' fonts put
    inside words ("Yield Differen ce", "A ran dom ization")."""
    return " ?".join(re.escape(c) for c in label.replace(" ", ""))


# (name, pattern, where the yields sit in what it matched, whole), tried in this
# order and named by the first season each was seen in. whole: the match is the
# table of treatment averages itself, so a reading takes all of it (the field's
# level is the mean of every treatment, even when ISA's list names two of five);
# otherwise the run can hold the difference or a neighbouring figure, and the
# list's count is tried before the run's own.
LAYOUTS = [
    # a report can hold two of these tables ("Table 2", then "Table 3" for a second
    # comparison in the same field, listed as its own "...B" trial)
    ("2023", re.compile(rf"{L('Treatment')} \| {L('Yield')} \| {L('Yield Group')} \| (.*?)"
                        rf"{L('Table')} ?\d", re.S), "grouped", True),
    ("2021", re.compile(rf"{L('Average')}{CELL}{L('for')}{CELL}{L('Treatments')}{CELL}"
                        rf"{L('(bu/ac)')} \| {RUN}"), "first", True),
    ("2018L", re.compile(rf"{LETTERED}{L('Treatments with the same letter')}"), "last", True),
    ("2015", re.compile(rf"{L('Yield Difference')} \| {RUN}{L('Yield Average for All')}"),
     "first", False),
    ("2018", re.compile(rf"{RUN}(?:{L('Yield Average for All')}|{L('A randomization')})"),
     "last", False),
    ("2013", re.compile(rf"{RUN}{L('Yield Difference')} \| {N} \|"), "last", False),
    ("2010", re.compile(rf"{RUN}{L('Percent of Trial')} \| {L('Yield (Bu/Ac)')} \| {L('Yield')} \| "
                        rf"{L('Difference')} \| {L('Yield By Treatment and Soil Type')} \| "
                        rf"{L('YIELD AVERAGE FOR TRIAL')}", re.I), "before_diff", False),
    ("2006", re.compile(rf"{L('YIELD AVERAGE FOR TRIAL')} \| {RUN}"), "first", False),
    ("2005", re.compile(rf"{RUN}{L('Yield Difference')} \| {L('Yield Average')}"), "last", False),
    ("2005b", re.compile(rf"{RUN}{L('Yield Average (Bu/a)')}"), "last", False),
    ("2005c", re.compile(rf"{L('Yield Average (Bu/a)')} \| {RUN}"), "first", False),
    ("2015b", re.compile(rf"{RUN}{L('Yield Averages')} \| \(bu/acre\)"), "last", False),
    # 2012 weed-control trials: a table of reps, then an "Avg <treatment>" row each
    ("2012w", re.compile(r"(\| (?i:avg) [A-Za-z].*)", re.S), "avg_rows", True),
]
READ = ("read", "unverified")               # statuses whose yields the page uses
# "Crop Rotation | Soybeans Following Corn", "...on a corn following soybeans rotation"
ROTATION = re.compile(r"(?i)crop\s?rotation:?\s?\|?\s?(corn|soy\s?beans?)\b"
                      r"|\bon an? (corn|soy\s?beans?) (?:following|after|on)\b")
CORN_FLOOR = 100.0                          # bu: a field under this is soybeans (see settle_crop)


def n_treatments(detail: str) -> int:
    """'A vs B vs C' -> 3 (2 at least)."""
    return max(2, len(re.split(r"\s+vs\.?\s+", detail or "", flags=re.I)))


def _flat(text: str) -> str:
    return " | ".join(t.strip() for t in re.split(r"[\n\f]+", text or "") if t.strip()) + " |"


def _numbers(run: str) -> list:
    return [float(x) for x in re.findall(N, run)]


def agrees(vals: list, response) -> bool:
    """Some pair of treatments is `response` apart (the only pair, for two).
    Whole-bushel figures (corn from 2023) can be a bushel off the listed response."""
    if response is None:
        return True
    tol = 1.01 if all(v == int(v) for v in vals) else 0.11
    return any(abs(abs(a - b) - abs(response)) <= tol
               for i, a in enumerate(vals) for b in vals[i + 1:])


def _cands(m, where: str, lo: float, hi: float):
    """What a match offers as treatment averages -> (values in order, which end a
    shorter reading takes, clean: nothing in the run but the values and at most
    the difference)."""
    if where == "grouped":                    # "Untreated | 77.7 | a | Source | 77.7 | a"
        return [float(v) for v in re.findall(rf"\| ({N}) \| [a-z]{{1,4}} \|",
                                             "| " + m.group(1))], "head", True
    if where == "avg_rows":                   # "Avg Untreated | 50.6 | <weed counts>"
        return [float(v) for v in re.findall(rf"\| (?i:avg) [A-Za-z][^|]* \| ({N}) \|",
                                             m.group(1))], "head", True
    nums = _numbers(m.group(1))
    if where in ("first", "last"):
        vals = [v for v in nums if lo <= v <= hi]
        return vals, "head" if where == "first" else "tail", len(nums) - len(vals) <= 1
    # before_diff: the run ends with the treatments' difference (in all 773 2010-12
    # readings), which for corn can be a plausible yield itself ("238.2 | 190.0 | 48.2")
    return nums[:-1], "tail", False


def parse(text: str, crop: str, detail: str, response=None):
    """-> (treatment averages, layout name, checked), or ([], None, False) when no
    reading fits. checked: some pair agrees with ISA's listed response."""
    flat, n = _flat(text), n_treatments(detail)
    lo, hi = RANGE[crop]

    def fits(vals):
        return 2 <= len(vals) <= MAX_TREATMENTS and all(lo <= v <= hi for v in vals)

    found = []                                # (layout, values, side, clean, whole)
    for name, pat, where, whole in LAYOUTS:
        for m in pat.finditer(flat):
            found.append((name, *_cands(m, where, lo, hi), whole))
    if response is not None:
        for name, vals, side, _, whole in found:      # a whole table, or the list's count
            k = vals if whole else (vals[:n] if side == "head" else vals[-n:])
            if (whole or len(vals) >= n) and fits(k) and agrees(k, response):
                return k, name, True
        for name, vals, side, _, whole in found:      # the run's own count
            if not whole and len(vals) != n and fits(vals) and agrees(vals, response):
                return vals, name, True
    for name, vals, side, clean, _ in found:          # unchecked: exactly the list's count
        if len(vals) == n and clean and fits(vals) and (n >= 3 or response is None):
            return vals, name, False
    return [], None, False


def report_crop(text: str):
    """The crop the report's rotation line names first, or None (most 2005-09
    reports have no such line)."""
    m = ROTATION.search(_flat(text))
    if not m:
        return None
    return "Corn" if (m.group(1) or m.group(2)).lower().startswith("corn") else "Soybeans"


def settle_crop(text: str, listed: str, detail: str, response):
    """-> (crop, parse result). ISA's list files some trials under the wrong crop
    (a dozen 2017-18 cover-crop trials listed as corn yield ~60 bu), and some
    reports' rotation lines are wrong the other way (a "soybean" field at 200 bu).
    Where the two disagree, the yields decide: a field averaging under CORN_FLOOR
    is soybeans, at or over it corn."""
    reading = parse(text, listed, detail, response)
    said = report_crop(text)
    if said is None or said == listed:
        return listed, reading
    other = parse(text, said, detail, response)
    vals = reading[0] or other[0]
    if not vals:
        return listed, reading
    level = "Soybeans" if sum(vals) / len(vals) < CORN_FLOOR else "Corn"
    return (said, other) if level == said and other[0] else (listed, reading)


def rows_from_cache(list_csv, text_dir) -> list:
    """The trial list joined with each cached report's reading. field_id names the
    report, because ISA lists some fields under two or more comparisons:
    "ST2013IA268A" and "...A1" share one report, and "ST2019IA0035a" has none of
    its own, only its base trial's ('same field'). The page counts each field once."""
    out = []
    with open(list_csv, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            crop = CROP.get(r["crop"])
            if crop is None:
                continue
            try:
                resp = float(r["avg_response"])
            except ValueError:
                resp = None
            path = os.path.join(text_dir, f"{r['trial_id']}.txt")
            vals, layout, field, listed = [], None, None, crop
            status = "no report"
            if os.path.exists(path):
                with open(path, encoding="utf-8") as t:
                    text = t.read()
                crop, (vals, layout, checked) = settle_crop(text, listed, r["trial_detail"], resp)
                status = "unread" if not vals else ("read" if checked else "unverified")
                field = hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
            out.append({
                "listed_crop": listed if listed != crop else None,
                "trial_id": r["trial_id"], "year": int(r["year"]), "crop": crop,
                "county": r["county"], "landform": r["landform"], "district": r["district"],
                "trial_type": r["trial_type"], "trial_detail": r["trial_detail"],
                "avg_response": resp, "treatments": len(vals) or n_treatments(r["trial_detail"]),
                "yields": " / ".join(f"{v:g}" for v in vals) or None,
                "trial_yield": sum(vals) / len(vals) if vals else None,
                "layout": layout, "status": status, "report_url": r["report_url"],
                "field_id": field})
    by_id = {o["trial_id"]: o for o in out}
    for o in out:
        base = by_id.get(o["trial_id"][:-1])
        if o["status"] == "no report" and o["trial_id"][-1:].islower() and base and base["field_id"]:
            o["status"], o["field_id"] = "same field", base["field_id"]
    return out


def replace(rows: list) -> int:
    cols = [c for c, _ in COLUMNS]
    conn, ph = db._connect()
    try:
        cur = conn.cursor()
        cur.execute(f"DROP TABLE IF EXISTS {TABLE}")
        cur.execute(f"CREATE TABLE {TABLE} (" + ", ".join(f"{c} {t}" for c, t in COLUMNS) + ")")
        cur.executemany(f"INSERT INTO {TABLE} ({', '.join(cols)}) VALUES ({', '.join([ph] * len(cols))})",
                        [tuple(db._clean(r.get(c)) for c in cols) for r in rows])
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def load_from_cache(list_csv, text_dir, target):
    """load_isa.py load: parse every cached report and replace the table."""
    if (target == "snowflake") != db.use_snowflake():
        raise SystemExit(f"refusing to run: --target {target} but the backend is {db.backend_name()}")
    rows = rows_from_cache(list_csv, text_dir)
    n = replace(rows)
    count = lambda *s: sum(r["status"] in s for r in rows)  # noqa: E731
    print(f"target: {db.backend_name()}\n  {TABLE}: {n} trials, {count(*READ)} read "
          f"({count('unverified')} of them unverified), {count('unread')} unread, "
          f"{count('same field')} another trial's field, {count('no report')} without a "
          f"report\n  fields read: {len({r['field_id'] for r in rows if r['status'] in READ})}")
    seasons = sorted({r["year"] for r in rows})
    print("  read by season: " + ", ".join(
        "%d %d/%d" % (y, sum(r["year"] == y and r["status"] in READ for r in rows),
                      sum(r["year"] == y and r["status"] in READ + ("unread",) for r in rows))
        for y in seasons))


def with_nass(d: pd.DataFrame, county_raw: pd.DataFrame, state_tbl: pd.DataFrame) -> pd.DataFrame:
    """+ county_final (the county's NASS yield that season, where NASS published
    it; cached from 2005), state_final (Iowa's final) and the field over each, as fractions
    (vs_county, vs_state). county_raw is nass.load's county frame, so an ISA county
    is matched by name ("O'Brien" = NASS "O BRIEN")."""
    d = d.copy()
    ia = county_raw[county_raw["state"] == "IA"]
    cy = {(c, int(y), places.norm(n)): v
          for c, y, n, v in ia[["crop", "year", "county", "yield"]].itertuples(index=False)}
    s = state_tbl[state_tbl["state"] == "IA"]
    sy = {(c, int(y)): v for c, y, v in s[["crop", "year", "final"]].itertuples(index=False)}
    d["county_final"] = pd.to_numeric(pd.Series(
        [cy.get((c, int(y), places.norm(n))) for c, y, n in zip(d["crop"], d["year"], d["county"])],
        index=d.index, dtype="object"), errors="coerce")
    d["state_final"] = pd.to_numeric(pd.Series(
        [sy.get((c, int(y))) for c, y in zip(d["crop"], d["year"])],
        index=d.index, dtype="object"), errors="coerce")
    d["vs_county"] = d["trial_yield"] / d["county_final"] - 1
    d["vs_state"] = d["trial_yield"] / d["state_final"] - 1
    return d


def fields(d: pd.DataFrame) -> pd.DataFrame:
    """The read trials, each field once: of the trials sharing a report, the one
    that read the most treatments (the fullest mean)."""
    r = d[d["status"].isin(READ)]
    return r.sort_values("treatments", ascending=False, kind="stable").drop_duplicates("field_id")


def by_season(d: pd.DataFrame) -> pd.DataFrame:
    """with_nass's frame -> crop, year: fields read, field (their mean yield), county
    (mean NASS yield of their counties, over the fields that have one), state
    (Iowa), and over_county / over_state (the median of the fields' own ratios)."""
    return (fields(d).groupby(["crop", "year"], as_index=False)
            .agg(fields=("trial_id", "size"), field=("trial_yield", "mean"),
                 county=("county_final", "mean"), state=("state_final", "max"),
                 over_county=("vs_county", "median"), over_state=("vs_state", "median")))


@st.cache_data(ttl=3600, show_spinner="Loading ISA strip trials…")
def load() -> pd.DataFrame:
    """The table, or an empty frame where it isn't loaded."""
    cols = [c for c, _ in COLUMNS]
    conn, _ = db._connect()
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT {', '.join(cols)} FROM {TABLE}")
        except Exception as exc:
            if "does not exist" in str(exc).lower() or "no such table" in str(exc).lower():
                return pd.DataFrame(columns=cols)
            raise
        d = pd.DataFrame(cur.fetchall(), columns=cols)
    finally:
        conn.close()
    for c in ("year", "treatments"):
        d[c] = pd.to_numeric(d[c], errors="coerce").astype("Int64")
    for c in ("avg_response", "trial_yield"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d
