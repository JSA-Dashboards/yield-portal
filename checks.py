"""
Checks that run on every load, not just at import: which reports a person
should look at before they count in averages, and the fix to suggest.

A check never changes a report. It raises a flag; a person then approves the
report as it is, applies the suggested fix, or excludes it (db.set_decision).
Flagged and excluded reports stay in the archive and on Report text, but stay
out of averages and charts until cleared. Approval covers the flags it was given
for: a new flag on an approved report (say after a parser change) asks again.
"""
import pandas as pd

import parse_pdfs as P

CHECKS = {
    "range": "Yield outside the usual range (corn 50–300 bpa, soybeans 10–100)",
    "corn?": "A soybean yield over 100 bpa, so probably a corn report",
    "reread": "The parser now reads a different figure from this report",
    "duplicate": "Same place, crop, year and yield as another report",
}
CORN_RANGE = (50, 300)
SOY_RANGE = (10, 100)
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


def reread(row) -> dict:
    """{field: value} the current parser reads differently from what's stored,
    or {} when neither the yield nor last year's yield would change."""
    m = P.extract_metrics(row["raw_text"] or "", row["crop"])
    changed = {f: m[f] for f in REREAD_FIELDS if not _same(row[f], m[f])}
    return changed if any(f in changed for f in _REREAD_FLAGS_ON) else {}


def _place_key(loc) -> str:
    import data                      # here: data imports this module
    return data.loc_key(loc)


def run(df: pd.DataFrame, decisions: dict) -> pd.DataFrame:
    """Add review_flags (codes), suggestion ({field: value} or None), dup_of (other
    hashes), decision and approved_flags (what a person decided), status (clean |
    flagged | approved | excluded | superseded) and in_analysis (counts in
    averages and charts)."""
    out = df.copy()
    n = len(out)
    flags = [[] for _ in range(n)]
    suggestion = [None] * n
    dup_of = [[] for _ in range(n)]
    decided = [decisions.get(h) or {} for h in out["dedup_hash"]]
    gone = [d.get("decision") in ("superseded", "excluded") for d in decided]

    for i, r in enumerate(out.to_dict("records")):
        if gone[i]:
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
        changed = reread(r)
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
            hashes = out.loc[members, "dedup_hash"].tolist()
            for idx, h in zip(members, hashes):
                flags[pos[idx]].append("duplicate")
                dup_of[pos[idx]] = [o for o in hashes if o != h]

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
    return out


def describe_fix(fix: dict) -> str:
    """'crop → Corn, yield 12 → 241' style text for a suggestion."""
    names = {"crop": "crop", "yield_bpa": "yield", "yield_min": "low", "yield_max": "high",
             "ly_yield": "last year", "expected_yield": "expected"}
    parts = []
    for k, v in (fix or {}).items():
        shown = "—" if _missing(v) else (f"{v:g}" if isinstance(v, float) else str(v))
        parts.append(f"{names.get(k, k)} → {shown}")
    return ", ".join(parts)
