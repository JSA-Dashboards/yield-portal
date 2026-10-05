"""
Iowa Soybean Association replicated on-farm strip trials: each trial's
treatment yields, read from the text of its report (load_isa.py fetches it), so
a trial field's yield can sit beside its county's NASS yield.

The reports changed layout several times between 2005 and 2025. `parse` tries
each layout's summary of treatment averages in turn and takes as many figures as
the trial has treatments, all in the crop's plausible range. Each reading is
checked against the average response ISA's trial list gives: the gap between
the treatments must match it (to the rounding of the figures). That check also
covers the list's treatment count being wrong for the report (it says "A vs B vs
C" where the report compared two), so when ISA lists a response, the other
counts are tried too. A report no reading agrees with is kept as 'unread', not
guessed at. The trial's yield is the mean of its treatment averages: treatments
mostly move yield a few bushels, and the field's level is what's compared with
NASS.

ISA states no terms beyond its copyright: the table stays internal (the Variety
trials page, never on the view link) and out of the public repo.
"""
import csv
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
           ("report_url", "VARCHAR(200)")]
CROP = {"Corn": "Corn", "Soybean": "Soybeans", "Soybeans": "Soybeans"}
RANGE = {"Corn": (40.0, 350.0), "Soybeans": (10.0, 120.0)}
N = r"-?\d{1,3}(?:\.\d+)?"                  # one number token
RUN = rf"((?:{N} \| ){{2,9}})"             # a run of number tokens, ' | '-separated

# (name, pattern, where the yields sit in the matched run of numbers)
LAYOUTS = [
    ("2023", re.compile(r"Treatment \| Yield \| Yield Group \| (.*?)Table 2", re.S), "grouped"),
    ("2021", re.compile(rf"Average for \|? ?Treatments ?\|? ?\(bu/ac\) \| {RUN}"), "first"),
    ("2015", re.compile(rf"Yield Difference \| {RUN}Yield Average for All"), "first"),
    ("2018", re.compile(rf"{RUN}(?:Yield Average for All|A randomization)"), "last"),
    ("2013", re.compile(rf"{RUN}Yield Difference \| {N} \|"), "last"),
    ("2010", re.compile(rf"{RUN}Percent of Trial \| Yield \(Bu/Ac\) \| Yield \| Difference \| "
                        r"Yield By Treatment and Soil Type \| YIELD AVERAGE FOR TRIAL", re.I), "before_diff"),
    ("2006", re.compile(rf"YIELD AVERAGE FOR TRIAL \| {RUN}"), "first"),
    ("2005", re.compile(rf"{RUN}Yield Difference \| Yield Average"), "last"),
    ("2005b", re.compile(rf"{RUN}Yield Average \(Bu/a\)"), "last"),
    ("2005c", re.compile(rf"Yield Average \(Bu/a\) \| {RUN}"), "first"),
    # 2012 weed-control trials: a table of reps, then an "Avg <treatment>" row each
    ("2012w", re.compile(r"Rep Treatment \| Yield \(bu/a\) \| (.*?)Paired TTEST", re.S), "avg_rows"),
]
READ = ("read", "unverified")               # statuses whose yields the page uses


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


def _take(m, where: str, k: int, lo: float, hi: float) -> list:
    if where == "grouped":                    # "Untreated | 77.7 | a | Source | 77.7 | a"
        return [float(v) for v in re.findall(rf"\| ({N}) \| [a-z]{{1,4}} \|", "| " + m.group(1))][:k]
    if where == "avg_rows":                   # "Avg Untreated | 50.6 | <weed counts>"
        return [float(v) for v in re.findall(rf"\| Avg [A-Za-z][^|]* \| ({N}) \|", "| " + m.group(1))][:k]
    nums = _numbers(m.group(1))
    if where == "first":
        return [v for v in nums if lo <= v <= hi][:k]
    if where == "last":
        return [v for v in nums if lo <= v <= hi][-k:]
    body = nums[:-1] if nums and not (lo <= nums[-1] <= hi) else nums    # last = the difference
    return body[-k:]


def parse(text: str, crop: str, detail: str, response=None):
    """-> (treatment averages, layout name), or ([], None) when no reading fits."""
    flat, n = _flat(text), n_treatments(detail)
    lo, hi = RANGE[crop]
    counts = [n] if response is None else [n] + [k for k in range(2, 7) if k != n]
    for k in counts:
        for name, pat, where in LAYOUTS:
            for m in pat.finditer(flat):
                vals = _take(m, where, k, lo, hi)
                if len(vals) == k and all(lo <= v <= hi for v in vals) and agrees(vals, response):
                    return vals, name
    return [], None


def rows_from_cache(list_csv, text_dir) -> list:
    """The trial list joined with each cached report's reading."""
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
            vals, layout = ([], None)
            status = "no report"
            if os.path.exists(path):
                with open(path, encoding="utf-8") as t:
                    vals, layout = parse(t.read(), crop, r["trial_detail"], resp)
                status = "unread" if not vals else ("read" if resp is not None else "unverified")
            out.append({
                "trial_id": r["trial_id"], "year": int(r["year"]), "crop": crop,
                "county": r["county"], "landform": r["landform"], "district": r["district"],
                "trial_type": r["trial_type"], "trial_detail": r["trial_detail"],
                "avg_response": resp, "treatments": len(vals) or n_treatments(r["trial_detail"]),
                "yields": " / ".join(f"{v:g}" for v in vals) or None,
                "trial_yield": sum(vals) / len(vals) if vals else None,
                "layout": layout, "status": status, "report_url": r["report_url"]})
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
    read = sum(r["status"] in READ for r in rows)
    print(f"target: {db.backend_name()}\n  {TABLE}: {n} trials, {read} read "
          f"({sum(r['status'] == 'unread' for r in rows)} unread, "
          f"{sum(r['status'] == 'no report' for r in rows)} without a cached report)")
    seasons = sorted({r["year"] for r in rows})
    print("  read by season: " + ", ".join(
        "%d %d/%d" % (y, sum(r["year"] == y and r["status"] in READ for r in rows),
                      sum(r["year"] == y and r["status"] != "no report" for r in rows))
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


def by_season(d: pd.DataFrame) -> pd.DataFrame:
    """with_nass's frame -> crop, year: trials read, field (their mean yield), county
    (mean NASS yield of their counties, over the trials that have one), state
    (Iowa), and over_county / over_state (the median of the trials' own ratios)."""
    r = d[d["status"].isin(READ)]
    return (r.groupby(["crop", "year"], as_index=False)
             .agg(trials=("trial_id", "size"), field=("trial_yield", "mean"),
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
