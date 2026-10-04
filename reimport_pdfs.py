"""
Re-read the annual PDFs with the current parser and replace archive rows that
the parser used to glue together.

    python reimport_pdfs.py --target sqlite                # dry run: report only
    python reimport_pdfs.py --target snowflake --apply     # write it

A PDF row the current parser no longer produces, whose text is fully made up of
two or more rows it does produce, was several reports run together (the 2023
layout put "Macon Co, IL 30 acres ..." on its own line with no separator, and
the parser read it as more of the report above). For each one:
  - the parts are inserted as new rows, carrying over the old row's report
    date, email and notes;
  - the old row is marked superseded in REVIEW_DECISIONS (never deleted), with
    the hashes of the rows that replaced it.
Rows whose text changed in any other way are only listed. Safe to re-run: a
second run finds nothing to do. Edited or dated rows are carried, not lost.
"""
import argparse
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env", override=True)
except ModuleNotFoundError:
    pass

import data                    # noqa: E402
import db                      # noqa: E402
import parse_pdfs as P         # noqa: E402

CARRY = ["date_reported", "email_subject", "email_id", "notes"]


def _norm(s):
    return re.sub(r"\s+", " ", str(s or "").lower()).strip()


def plan(pdf_dir=P.DL):
    """-> (splits [(old_row, [part rows])], unexplained [old_row])."""
    archive = data.frame()
    pdf_rows = archive[(archive["report_source"] == "pdf") & (archive["status"] != "superseded")]
    parsed = []
    for fname, year in P.FILES.items():
        path = os.path.join(pdf_dir, fname)
        if not os.path.exists(path):
            sys.exit(f"Missing {path}: every PDF is needed to tell a split from a deletion.")
        parsed += P.parse_pdf(path, year)
    produced = {r["dedup_hash"] for r in parsed}
    stored = set(archive["dedup_hash"])
    fresh = [r for r in {r["dedup_hash"]: r for r in parsed}.values() if r["dedup_hash"] not in stored]

    splits, unexplained = [], []
    for old in pdf_rows[~pdf_rows["dedup_hash"].isin(produced)].to_dict("records"):
        text = _norm(old["raw_text"])
        parts = [r for r in fresh if r["crop_year"] == old["crop_year"] and r["crop"] == old["crop"]
                 and r.get("source_file") == old["source_file"] and _norm(r["raw_text"]) in text]
        covered = sum(len(_norm(r["raw_text"])) for r in parts)
        if len(parts) >= 2 and covered >= 0.9 * len(text):
            splits.append((old, parts))
        else:
            unexplained.append(old)
    return splits, unexplained


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["sqlite", "snowflake"], required=True)
    ap.add_argument("--connection", help="Snowflake profile in ~/.snowflake/connections.toml")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--pdf-dir", default=P.DL)
    args = ap.parse_args()
    if args.target == "snowflake":
        os.environ["USE_SNOWFLAKE"] = "1"
        if args.connection:
            os.environ["SNOWFLAKE_CONNECTION_NAME"] = args.connection
    else:
        os.environ.pop("USE_SNOWFLAKE", None)
    if db.use_snowflake() != (args.target == "snowflake"):
        sys.exit(f"Backend mismatch: asked {args.target}, got {db.backend_name()}.")
    print(f"{'APPLYING to' if args.apply else 'DRY RUN against'} {db.backend_name()}\n")

    splits, unexplained = plan(args.pdf_dir)
    n_parts = sum(len(p) for _, p in splits)
    print(f"{len(splits)} archive rows hold several reports -> {n_parts} reports")
    for old, parts in splits:
        print(f"\n  {old['crop_year']} {old['crop']} {old['state']} {old['location']}  ->  {len(parts)} reports")
        for p in parts:
            y = f"{p['yield_bpa']:g}" if p.get("yield_bpa") is not None else "-"
            print(f"      {p['state']} {str(p['location'])[:24]:24} {y:>6}  {p['raw_text'][:70]}")
    if unexplained:
        print(f"\n{len(unexplained)} PDF rows the parser no longer produces, not explained by a split "
              f"(left alone):")
        for old in unexplained:
            print(f"  {old['crop_year']} {old['state']} {old['location']} | {old['raw_text'][:90]}")
    if not args.apply:
        print("\nDry run: nothing written. Re-run with --apply to write.")
        return

    db.ensure_review_table()
    inserted = 0
    for old, parts in splits:
        for p in parts:
            for c in CARRY:
                if old.get(c) is not None and not (isinstance(old.get(c), float) and old[c] != old[c]):
                    p[c] = old[c]
        ins, _ = db.insert_new(parts)
        inserted += ins
        db.set_decision(old["dedup_hash"], "superseded", note="split into " + ", ".join(
            p["dedup_hash"] for p in parts), decided_by="reimport_pdfs.py")
    data.invalidate()
    print(f"\nInserted {inserted} reports; marked {len(splits)} merged rows superseded.")


if __name__ == "__main__":
    main()
