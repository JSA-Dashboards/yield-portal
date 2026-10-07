"""
Checks that run on every load, not just at import: which reports a person
should look at before they count in averages, and the fix to suggest.

A check never changes a report. It raises a flag; a person then approves the
report as it is, applies the suggested fix, or excludes it (db.set_decision).
Flagged and excluded reports stay in the archive and on Report text, but stay
out of averages and charts until cleared. Approval covers the flags it was given
for: a new flag on an approved report (say after a parser change) asks again.
"""
import re

import pandas as pd

import parse_pdfs as P

CHECKS = {
    "incomplete": "The email didn't say which crop or state: set them by hand "
                  "(corn is suggested when the yield is over 100 bpa)",
    "split": "Gives yields for both corn and soybeans: one report per crop is suggested",
    "entries": "Gives yields for several fields, or for dryland and irrigated ground: one "
               "report per entry is suggested, each with the whole text and its own figures",
    "range": "Yield outside the usual range (corn 50–300 bpa, soybeans 10–100)",
    "corn?": "A soybean yield over 100 bpa, so probably a corn report",
    "reread": "The parser now reads a different figure from this report",
    "duplicate": "Same place, crop, year and yield as another report",
}
CORN_RANGE = (50, 300)
SOY_RANGE = (10, P.SOY_MAX)
# What the parser extracts and a re-read can change; only the figures the
# analysis uses raise a flag.
REREAD_FIELDS = ["yield_bpa", "yield_min", "yield_max", "ly_yield", "expected_yield"]
_REREAD_FLAGS_ON = ("yield_bpa", "ly_yield")


def _missing(v):
    return v is None or (isinstance(v, float) and pd.isna(v))


def _same(a, b):
    if _missing(a) or _missing(b):
        return _missing(a) and _missing(b)
    return abs(float(a) - float(b)) < 1e-9


def _equal(a, b):
    """_same for any stored value: numbers, flags and words."""
    if isinstance(a, str) or isinstance(b, str):
        return (None if _missing(a) else a) == (None if _missing(b) else b)
    return _same(a, b)


# What a stored row that already is one of a report's entries takes from that entry
# as read today: an irrigated half split before entries read their own figures can
# carry the report's first APH instead of its own.
ENTRY_FIELDS = ["yield_min", "yield_max", "ly_yield", "expected_yield", "aph",
                "irrigation", "is_silage", "is_record"]


def entry_changes(entry: dict, stored: dict) -> dict:
    """{field: value} where a stored entry row differs from its entry's reading.
    Only when the yields agree: a row whose yield differs was read or corrected
    otherwise, and stays as it is."""
    if not _same(entry.get("yield_bpa"), stored.get("yield_bpa")):
        return {}
    return {f: entry.get(f) for f in ENTRY_FIELDS if not _equal(entry.get(f), stored.get(f))}


def _practice(row):
    """The row's practice when it's one ('Irrigated' / 'Non-irrigated')."""
    v = row.get("irrigation")
    return v if v in P.PRACTICES else None


def _entries(row):
    """P.entries_of for a row: computed ahead by row_checks, else here."""
    return row["_entries"] if "_entries" in row else P.entries_of(row["raw_text"] or "", row["crop"])


def entry_hash(row, part) -> str:
    """The hash of one entry of a row's report. None, not NaN, for a blank field:
    the parser's hashes have "None" in the key."""
    return P.dedup_hash(*(None if _missing(row[k]) else row[k]
                          for k in ("crop_year", "crop", "state", "location")),
                        row["raw_text"] or "", part)


def field_of(row, entries=None):
    """(i, n, words) when a row is one of the n entries its report gives: the i-th,
    in its own words. None for a report that is one entry (or not split yet). Every
    entry row carries the whole report's text, so this says which field it is."""
    entries = _entries(row) if entries is None else entries
    found = P.split_entries(row["raw_text"] or "") if entries else None
    if not found:
        return None
    for i, ((part, _), (_, words)) in enumerate(zip(entries, found[1]), 1):
        if entry_hash(row, part) == row["dedup_hash"]:
            return i, len(entries), words
    return None


def field_acres(row, field):
    """The acreage a row's field names (its entry's first), or a whole report's
    only one; None when it names none, or several."""
    words = field[2] if field else (row["raw_text"] or "")
    hits = [m for m in P._ACRES_RE.finditer(words) if not P._ly_field(words, m.start())]
    if not hits or (not field and len(hits) > 1):
        return None
    return float(re.match(r"\d[\d,]*(?:\.\d+)?", hits[0].group()).group().replace(",", ""))


def reread(row, entries=None) -> dict:
    """{field: value} the current parser reads differently from what's stored,
    or {} when neither the yield nor the prior year's would change. A report
    split by field or practice is read as the entry this row is (its practice
    and yield); a row that matches no entry is a report not split yet, which the
    'entries' check covers."""
    entries = _entries(row) if entries is None else entries
    if entries:
        # its own entry by hash: two fields of one report can share a yield
        # ("40 acres made 50 bpa vs 61 ... 90 acres made 50 bpa vs 57")
        m = next((m for part, m in entries if entry_hash(row, part) == row["dedup_hash"]), None)
        if m is None:
            m = next((m for _, m in entries if _same(m["yield_bpa"], row["yield_bpa"])
                      and _practice(row) in (None, m["irrigation"])), None)
        if m is None:
            return {}
    else:
        m = P.extract_metrics(row["raw_text"] or "", row["crop"])
    changed = {f: m[f] for f in REREAD_FIELDS if not _same(row[f], m[f])}
    return changed if any(f in changed for f in _REREAD_FLAGS_ON) else {}


def row_checks(df: pd.DataFrame) -> pd.DataFrame:
    """The checks that depend on a report's own text alone (the re-read, the
    two-crop split, the entries), computed once with the cached reports so a
    review decision doesn't re-parse every report. run() computes them itself
    when they're absent."""
    out = df.copy()
    recs = out.to_dict("records")
    entries = [P.entries_of(r["raw_text"] or "", r["crop"]) for r in recs]
    out["_entries"] = entries
    out["_reread"] = [reread(r, e) for r, e in zip(recs, entries)]
    out["_split"] = [P.split_by_crop(r["raw_text"] or "", r["crop"]) for r in recs]
    out["_field"] = [field_of(r, e) for r, e in zip(recs, entries)]
    return out


def split_rows(r) -> list:
    """The reports a 'split' suggestion makes: one per crop, each with its own
    sentences, figures and hash, carrying the original's place, source, date,
    email and notes."""
    out = []
    for crop, text in (r["suggestion"] or {}).get("split", {}).items():
        child = {k: r.get(k) for k in ("crop_year", "state", "location", "report_source",
                                       "source_file", "date_reported", "email_subject",
                                       "email_id", "notes")}
        child.update(P.extract_metrics(text, crop))
        child.update(crop=crop, raw_text=text,
                     dedup_hash=P.dedup_hash(r["crop_year"], crop, r["state"], r["location"], text))
        out.append(child)
    return out


def entry_rows(r) -> list:
    """The reports an 'entries' suggestion makes: one per entry, each the whole
    report with its own entry's figures and practice, carrying the original's
    place, crop, source, date, email and notes. A maturity or disease a person
    keyed in stays when the text doesn't give one."""
    # None, not NaN, for a blank field: the hashes must be the ones the parser makes
    # ("None" in the key), or a re-read of the same email would add the entries again
    base = {k: None if _missing(r.get(k)) else r.get(k)
            for k in ("crop_year", "crop", "state", "location", "raw_text", "report_source",
                      "source_file", "date_reported", "email_subject", "email_id", "notes")}
    kids = P.by_entry(base)
    for k in kids:
        for f in ("maturity", "disease"):
            if _missing(k.get(f)) and not _missing(r.get(f)):
                k[f] = r[f]
    return kids


def _place_key(loc) -> str:
    import data                      # here: data imports this module
    return data.loc_key(loc)


def run(df: pd.DataFrame, decisions: dict) -> pd.DataFrame:
    """Add review_flags (codes), suggestion ({field: value} or None), dup_of (other
    hashes), decision and approved_flags (what a person decided), status (clean |
    flagged | approved | excluded | superseded), in_analysis (counts in
    averages and charts) and field ("3 of 4: 40a 52bpa ...": which field a row
    is when its report gives several, each stored as its own report)."""
    out = df.copy()
    n = len(out)
    flags = [[] for _ in range(n)]
    suggestion = [None] * n
    dup_of = [[] for _ in range(n)]
    decided = [decisions.get(h) or {} for h in out["dedup_hash"]]
    gone = [d.get("decision") in ("superseded", "excluded") for d in decided]
    # the live rows each report's text has become (the entries of a split report
    # share its text)
    ident = list(zip(out["crop_year"], out["crop"], out["state"], out["location"].astype(str),
                     out["raw_text"].astype(str)))
    siblings = {}
    for i, k in enumerate(ident):
        if not gone[i]:
            siblings.setdefault(k, []).append(out["dedup_hash"].iloc[i])
    records = out.to_dict("records")
    by_hash = {r["dedup_hash"]: r for r in records}
    fields = [r["_field"] if "_field" in r else field_of(r) for r in records]

    for i, r in enumerate(records):
        if gone[i]:
            continue
        if r["crop"] not in ("Corn", "Soybeans") or _missing(r["state"]) or not r["state"]:
            flags[i].append("incomplete")     # an auto-loaded email nobody could place
            if (r["crop"] not in ("Corn", "Soybeans") and isinstance(r["state"], str)
                    and r["state"] and not _missing(r["yield_bpa"])
                    and r["yield_bpa"] > SOY_RANGE[1]):
                suggestion[i] = {"crop": "Corn"}    # only corn yields run past 100 bpa
            continue
        parts = r["_split"] if "_split" in r else P.split_by_crop(r["raw_text"] or "", r["crop"])
        if parts:                    # the split answers the crop and the figures
            flags[i].append("split")
            suggestion[i] = {"split": parts}
            continue
        # several fields, or dryland and irrigated, in one report that hasn't
        # become as many rows yet (a dryland half split earlier can hold two fields)
        entries, mine = _entries(r), siblings.get(ident[i], [])
        if entries and len(entries) > len(mine):
            texts = [t for _, t in P.split_entries(r["raw_text"] or "")[1]]
            flags[i].append("entries")
            # a row that already is one of the entries stays, with its entry's figures
            updates = {}
            for part, m in entries:
                h = entry_hash(r, part)
                if h in mine and (ch := entry_changes(m, by_hash[h])):
                    updates[h] = ch
            suggestion[i] = {"entries": [[part, m["irrigation"], m["yield_bpa"], t]
                                         for (part, m), t in zip(entries, texts)],
                             "siblings": mine, "updates": updates}
            continue
        fix = {}
        y = r["yield_bpa"]
        if not _missing(y):
            if r["crop"] == "Soybeans" and y > SOY_RANGE[1]:
                flags[i].append("corn?")
                fix["crop"] = "Corn"
            elif ((r["crop"] == "Corn" and not CORN_RANGE[0] <= y <= CORN_RANGE[1])
                  or (r["crop"] == "Soybeans" and y < SOY_RANGE[0])):
                flags[i].append("range")
        # one of several rows of a text the parser no longer splits (split by an
        # older rule, or by hand): its whole-report reading isn't this row's
        changed = {} if (not entries and len(mine) > 1) else (
            r["_reread"] if "_reread" in r else reread(r))
        if changed:
            flags[i].append("reread")
            fix.update(changed)
        suggestion[i] = fix or None

    # the same report twice: place, crop, year and yield all equal
    alive = pd.Series([not g for g in gone], index=out.index, dtype=bool)
    live = out[alive & out["yield_bpa"].notna()]
    keys = list(zip(live["crop_year"], live["crop"], live["state"],
                    live["location"].map(_place_key), live["yield_bpa"]))
    groups = {}
    for idx, k in zip(live.index, keys):
        groups.setdefault(k, []).append(idx)
    pos = {idx: i for i, idx in enumerate(out.index)}
    for members in groups.values():
        if len(members) > 1:
            hashes = dict(zip(members, out.loc[members, "dedup_hash"]))
            practice = {idx: _practice(out.loc[idx]) for idx in members}
            text = {idx: out.at[idx, "raw_text"] for idx in members}
            acres = {idx: field_acres(records[pos[idx]], fields[pos[idx]]) for idx in members}
            for idx in members:
                # entries of one report (one text) aren't each other's duplicate, nor
                # are dryland and irrigated reports of one place's harvest, nor fields
                # of different sizes ("40a 52bpa" in one report, "80 acres went 52" in
                # another): a field reported twice names the same acres
                twins = [hashes[o] for o in members if o != idx and text[o] != text[idx]
                         and not (practice[idx] and practice[o] and practice[idx] != practice[o])
                         and not (acres[idx] and acres[o] and acres[idx] != acres[o])]
                if twins:
                    flags[pos[idx]].append("duplicate")
                    dup_of[pos[idx]] = twins

    status, approved_flags = [], []
    for f, d in zip(flags, decided):
        dec = d.get("decision")
        cleared = {c for c in (d.get("flags") or "").split(", ") if c} if dec == "approved" else set()
        approved_flags.append(sorted(cleared))
        if dec in ("superseded", "excluded"):
            status.append(dec)
        elif f and not set(f) <= cleared:
            status.append("flagged")
        else:
            status.append("approved" if dec == "approved" else "clean")
    out["review_flags"] = flags
    out["suggestion"] = suggestion
    out["dup_of"] = dup_of
    out["decision"] = [d.get("decision") for d in decided]
    out["approved_flags"] = approved_flags
    out["status"] = status
    out["in_analysis"] = out["status"].isin(["clean", "approved"]) & out["yield_bpa"].notna()
    # which field a row is, when its report gives several: "3 of 4: 40a 52bpa ..."
    out["field"] = [f"{f[0]} of {f[1]}: {f[2]}" if f else None for f in fields]
    return out


def describe_fix(fix: dict) -> str:
    """'crop → Corn, yield 12 → 241' style text for a suggestion."""
    if fix and "split" in fix:
        return "split into " + " + ".join(
            f"{crop} {P.extract_metrics(text, crop)['yield_bpa']:g} bpa"
            for crop, text in fix["split"].items())
    if fix and "entries" in fix:
        kind = {"Non-irrigated": "dryland", "Irrigated": "irrigated"}
        return f"split into {len(fix['entries'])} reports: " + " + ".join(
            f"{kind.get(p, 'field')} {y:g}" for _, p, y, _ in fix["entries"]) + " bpa"
    names = {"crop": "crop", "yield_bpa": "yield", "yield_min": "low", "yield_max": "high",
             "ly_yield": "prior year", "expected_yield": "expected"}
    parts = []
    for k, v in (fix or {}).items():
        shown = "—" if _missing(v) else (f"{v:g}" if isinstance(v, float) else str(v))
        parts.append(f"{names.get(k, k)} → {shown}")
    return ", ".join(parts)
