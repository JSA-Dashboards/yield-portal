"""
Load the university variety-trial tables into the portal's database.

    python load_trials.py                              # local SQLite
    python load_trials.py --target snowflake           # the .env service login
    python load_trials.py --target snowflake --connection <profile>

Reads portal_site_years.csv and portal_state_years.csv, which
`illinois-corn-trials/export_portal_tables.py` writes. Both tables are replaced
wholesale: a reload follows an upstream parser change, and the point of it is that a
site-year's figure can legitimately change when the extractor improves.

The CSVs are not committed to this repo. It is public, and Ohio's programme has not
given written permission for derived use, so the trial data lives only in the database
and only behind the portal's password — never on the ?view=1 link.
"""
import argparse
import os
import pathlib
import sys

import pandas as pd

PROJ = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(PROJ))
try:
    from dotenv import load_dotenv
    load_dotenv(PROJ / ".env", override=True)
except ModuleNotFoundError:
    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=("sqlite", "snowflake"), default="sqlite")
    ap.add_argument("--connection", help="profile in ~/.snowflake/connections.toml")
    ap.add_argument("--data-dir", help="where the exported CSVs are")
    args = ap.parse_args()

    # Set before db is imported: it reads USE_SNOWFLAKE at call time, but being
    # explicit here is what keeps a 'load' from silently writing to the local file
    # when .env happens to be missing.
    os.environ["USE_SNOWFLAKE"] = "1" if args.target == "snowflake" else ""
    if args.connection:
        os.environ["SNOWFLAKE_CONNECTION_NAME"] = args.connection

    import db
    import trials
    if (args.target == "snowflake") != db.use_snowflake():
        sys.exit("refusing to run: --target %s but the backend resolved to %s"
                 % (args.target, db.backend_name()))

    data_dir = args.data_dir or trials.source_csvs()
    pairs = ((trials.SITE_TABLE, "portal_site_years.csv"),
             (trials.STATE_TABLE, "portal_state_years.csv"))
    missing = [f for _t, f in pairs if not os.path.exists(os.path.join(data_dir, f))]
    if missing:
        sys.exit("%s not in %s — run export_portal_tables.py in illinois-corn-trials "
                 "first (or pass --data-dir)" % (", ".join(missing), data_dir))

    print("target: %s" % db.backend_name())
    for table, fname in pairs:
        d = pd.read_csv(os.path.join(data_dir, fname))
        cols = [c for c, _ in trials.TABLES[table]]
        for c in cols:
            if c not in d:
                d[c] = None
        n = trials.replace(table, d[cols].to_dict("records"))
        print("  %-18s %5d rows  (%s)" % (table, n, fname))


if __name__ == "__main__":
    main()
