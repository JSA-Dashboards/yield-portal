"""
Load the county geography (geo.py) into the portal's database.

    python load_geo.py                       # local SQLite
    python load_geo.py --target snowflake    # the .env service login

Downloads the Census Bureau's 2023 county gazetteer (centre points) and county
adjacency file (shared borders) to a temporary folder, builds one row per
county, and replaces COUNTY_GEO. Public-domain reference data, so nothing here
is secret; it's still not committed, to keep the repo small.
"""
import argparse
import io
import os
import pathlib
import sys
import tempfile
import urllib.request
import zipfile

PROJ = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(PROJ))
try:
    from dotenv import load_dotenv
    load_dotenv(PROJ / ".env", override=True)
except ModuleNotFoundError:
    pass


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (yield-portal)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=("sqlite", "snowflake"), default="sqlite")
    ap.add_argument("--connection", help="profile in ~/.snowflake/connections.toml")
    args = ap.parse_args()
    os.environ["USE_SNOWFLAKE"] = "1" if args.target == "snowflake" else ""
    if args.connection:
        os.environ["SNOWFLAKE_CONNECTION_NAME"] = args.connection

    import db
    import geo
    if (args.target == "snowflake") != db.use_snowflake():
        sys.exit(f"refusing to run: --target {args.target} but the backend resolved to "
                 f"{db.backend_name()}")

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(io.BytesIO(_get(geo.GAZETTEER_URL))) as z:
            name = next(n for n in z.namelist() if n.endswith(".txt"))
            gaz = pathlib.Path(tmp) / "gazetteer.txt"
            gaz.write_bytes(z.read(name))
        adj = pathlib.Path(tmp) / "adjacency.txt"
        adj.write_bytes(_get(geo.ADJACENCY_URL))
        frame = geo.from_census(gaz, adj)
    n = geo.replace(frame)
    print(f"target: {db.backend_name()}\n  {geo.TABLE}: {n} counties "
          f"({int((frame['neighbors'] != '').sum())} with neighbors)")


if __name__ == "__main__":
    main()
