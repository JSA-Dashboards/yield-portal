"""
Prove the Yield Portal's service login works — the same key-pair path the
Streamlit app uses — with no browser.

    python snowflake/verify_service.py

Checks the login as YIELD_PORTAL_SVC, read access (row count), and the CREATE
TABLE grant that imports need (a temporary table, dropped at once). Run it after
creating the service user (snowflake/service_user.sql) and after any key
rotation. Never prints key content.
"""
import argparse
import os
import pathlib
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=str(pathlib.Path.home() / ".snowflake" / "yield_portal_svc.p8"))
    ap.add_argument("--account", default=os.environ.get("SNOWFLAKE_ACCOUNT"),
                    help="account identifier (default: SNOWFLAKE_ACCOUNT)")
    ap.add_argument("--user", default="YIELD_PORTAL_SVC")
    ap.add_argument("--role", default="YIELD_PORTAL_ROLE")
    ap.add_argument("--warehouse", default="COMPUTE_WH")
    args = ap.parse_args()
    if not args.account:
        sys.exit("Pass --account (or set SNOWFLAKE_ACCOUNT).")

    os.environ.pop("SNOWFLAKE_CONNECTION_NAME", None)   # the app's path, not a profile
    os.environ.update(USE_SNOWFLAKE="1", SNOWFLAKE_ACCOUNT=args.account,
                      SNOWFLAKE_USER=args.user, SNOWFLAKE_ROLE=args.role,
                      SNOWFLAKE_WAREHOUSE=args.warehouse,
                      SNOWFLAKE_PRIVATE_KEY_PATH=args.key)
    os.environ.pop("SNOWFLAKE_PRIVATE_KEY", None)
    os.environ.pop("SNOWFLAKE_PASSWORD", None)
    import db

    conn = db.sf_connect()
    cur = conn.cursor()
    cur.execute("SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE()")
    print("Logged in: user=%s role=%s warehouse=%s" % cur.fetchone())
    cur.execute(f"SELECT COUNT(*), COUNT(date_reported) FROM {db.TABLE}")
    total, dated = cur.fetchone()
    print(f"Read OK: {total} reports, {dated} with a report date")
    cur.execute(f"CREATE TEMPORARY TABLE _verify_stage LIKE {db.TABLE}")
    cur.execute("DROP TABLE _verify_stage")
    print("Write path OK: can stage a temporary table (imports will work)")
    conn.close()


if __name__ == "__main__":
    main()
