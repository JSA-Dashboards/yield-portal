"""
Load the annual Ag Trader Talk yield PDFs into the portal database.

    python load_pdfs.py --target sqlite        # local dev file
    python load_pdfs.py --target snowflake     # YIELD_REPORTS on the Analytics account

Safe to re-run: rows are keyed by dedup_hash and only unseen rows are inserted,
so a second run inserts 0 and never overwrites rows edited in the portal.

The --target flag is checked against the backend db.py actually selected, so a
missing/unread .env can't silently send a "Snowflake" load into local SQLite.
"""
import argparse
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent

# Load THIS project's .env by absolute path (not whatever the cwd happens to be)
# before importing db, which reads USE_SNOWFLAKE at call time.
try:
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env", override=True)
except ModuleNotFoundError:
    pass

import db                      # noqa: E402
import parse_pdfs as P         # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["sqlite", "snowflake"], required=True)
    ap.add_argument("--connection",
                    help="Snowflake profile in ~/.snowflake/connections.toml")
    ap.add_argument("--pdf-dir", default=P.DL,
                    help="folder holding the yearly PDFs (default: Downloads)")
    args = ap.parse_args()

    if args.target == "snowflake":
        os.environ["USE_SNOWFLAKE"] = "1"
        if args.connection:
            os.environ["SNOWFLAKE_CONNECTION_NAME"] = args.connection
    else:
        os.environ.pop("USE_SNOWFLAKE", None)
    want_sf = args.target == "snowflake"
    if db.use_snowflake() != want_sf:
        sys.exit(f"Backend mismatch: asked for {args.target}, db.py selected "
                 f"{db.backend_name()}. Aborting before any write.")
    print("Target:", db.backend_name())

    db.init_db()
    before = db.count_rows()
    total_in = total_skip = 0
    for fname, year in P.FILES.items():
        path = pathlib.Path(args.pdf_dir) / fname
        if not path.exists():
            print(f"  ! missing {path} — skipped")
            continue
        rows = P.parse_pdf(str(path), year)
        ins, skip = db.insert_new(rows)
        total_in += ins
        total_skip += skip
        print(f"  {fname}: {len(rows)} parsed -> {ins} inserted, {skip} already stored")
    after = db.count_rows()
    print(f"\nInserted {total_in}, skipped {total_skip}. "
          f"Table rows: {before} -> {after}")


if __name__ == "__main__":
    main()
