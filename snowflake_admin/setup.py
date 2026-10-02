"""
One-time setup of the Yield Portal on the Analytics Snowflake account, then a
copy of everything in the local SQLite archive into it — all over ONE
connection, so a browser sign-in happens once.

    python snowflake_admin/setup.py --connection <profile> --check  # prove the sign-in
    python snowflake_admin/setup.py --connection <profile>          # create + copy

--connection names a profile in ~/.snowflake/connections.toml (the Analytics
account's signs in through the browser); without it, SNOWFLAKE_* from this
project's .env are used. The copy comes from the local archive (not the PDFs),
so report dates and portal edits come across. Safe to re-run: only rows whose
dedup_hash isn't in Snowflake yet are inserted.

Needs a warehouse for the inserts: --warehouse, else the session default, else
an existing COMPUTE_WH, else the account's only warehouse. It creates one only
when asked (--create-warehouse NAME; billable, so XSMALL, suspends after 60 s
idle, resumes on demand) — otherwise with none available it stops before
writing anything. On the Analytics account COMPUTE_WH already exists (visible as
ACCOUNTADMIN, but nobody's default warehouse), so setup used it on 2026-10-02.
"""
import argparse
import os
import pathlib
import sqlite3
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJ / ".env", override=True)
os.environ["USE_SNOWFLAKE"] = "1"
import db  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="connect and report, nothing else")
    ap.add_argument("--connection", help="profile name in ~/.snowflake/connections.toml")
    ap.add_argument("--warehouse", help="warehouse to use for the inserts")
    ap.add_argument("--create-warehouse", metavar="NAME",
                    help="create this XSMALL auto-suspending warehouse if it doesn't exist")
    args = ap.parse_args()
    if args.connection:
        os.environ["SNOWFLAKE_CONNECTION_NAME"] = args.connection

    conn = db.sf_connect(database=None, schema=None)
    cur = conn.cursor()
    cur.execute("SELECT CURRENT_ACCOUNT(), CURRENT_REGION(), CURRENT_ROLE(), CURRENT_WAREHOUSE()")
    acct, region, role, current_wh = cur.fetchone()
    cur.execute("SHOW WAREHOUSES")
    warehouses = [r[0] for r in cur.fetchall()]
    print(f"Connected: account={acct} region={region} role={role} "
          f"warehouse={current_wh} | warehouses on the account: {warehouses or 'none'}")
    if args.check:
        return

    if args.create_warehouse and args.create_warehouse.upper() not in warehouses:
        cur.execute(
            f"CREATE WAREHOUSE IF NOT EXISTS {args.create_warehouse} WITH "
            "WAREHOUSE_SIZE = 'XSMALL' AUTO_SUSPEND = 60 AUTO_RESUME = TRUE "
            "INITIALLY_SUSPENDED = TRUE "
            "COMMENT = 'General compute; created for the Yield Portal (XSMALL, 60s auto-suspend)'")
        print(f"Created warehouse {args.create_warehouse.upper()} (XSMALL, auto-suspend 60 s)")
        warehouses.append(args.create_warehouse.upper())
        args.warehouse = args.warehouse or args.create_warehouse

    wh = (args.warehouse or current_wh
          or ("COMPUTE_WH" if "COMPUTE_WH" in warehouses else None)
          or (warehouses[0] if len(warehouses) == 1 else None))
    if not wh:
        sys.exit("No warehouse to run the inserts on — pass --warehouse NAME "
                 f"(available: {warehouses or 'none'}). Nothing was created.")
    cur.execute(f'USE WAREHOUSE "{wh}"')

    cur.execute(f"CREATE DATABASE IF NOT EXISTS {db.SF_DATABASE}")
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {db.SF_DATABASE}.{db.SF_SCHEMA}")
    cur.execute(f"USE SCHEMA {db.SF_DATABASE}.{db.SF_SCHEMA}")
    cur.execute(db._ddl())
    print(f"Ready: {db.SF_DATABASE}.{db.SF_SCHEMA}.{db.TABLE} (warehouse {wh})")

    names = list(db.COL_NAMES)
    local = sqlite3.connect(db.LOCAL_SQLITE)
    rows = [dict(zip(names, r)) for r in
            local.execute(f"SELECT {', '.join(names)} FROM {db.TABLE}").fetchall()]
    for r in rows:                              # SQLite keeps booleans as 0/1
        for b in ("is_silage", "is_record"):
            r[b] = bool(r[b]) if r[b] is not None else None

    cur.execute(f"SELECT dedup_hash FROM {db.TABLE}")
    have = {h for (h,) in cur.fetchall()}
    fresh = [r for r in rows if r["dedup_hash"] not in have]
    if fresh:
        marks = ", ".join(["%s"] * len(names))
        cur.executemany(f"INSERT INTO {db.TABLE} ({', '.join(names)}) VALUES ({marks})",
                        [tuple(db._clean(r[c]) for c in names) for r in fresh])
        conn.commit()
    cur.execute(f"SELECT COUNT(*), COUNT(date_reported) FROM {db.TABLE}")
    total, dated = cur.fetchone()
    print(f"Copied {len(fresh)} new row(s) ({len(rows) - len(fresh)} already there). "
          f"Snowflake now holds {total} reports, {dated} with a report date.")
    conn.close()


if __name__ == "__main__":
    main()
