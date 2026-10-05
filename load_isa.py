"""
Iowa Soybean Association replicated on-farm strip trials -> the portal.

    python load_isa.py fetch            # trial list + each report's text, into data/isa/
    python load_isa.py load [--target snowflake]   # read the cached text (isa_trials.py),
                                                   # replace ISA_STRIP_TRIALS (.env login)

The trial list comes from ISA's public strip-trial database
(onlinedb.iasoybeans.com), one year at a time (asking for every year at once
makes its server fail). Each trial's yields are only in its PDF report, so
`fetch` downloads the reports one at a time with a pause between them, keeps
just their text (data/isa/text/<trial id>.txt, pages split by form feeds) and
never the PDFs, and skips any trial already cached. A parser change then needs
no second download. About 4,800 trials, 2005-2025; a full first run takes a few
hours, so run it on the droplet.

ISA states no terms beyond its copyright and a disclaimer that it doesn't endorse
the products tested; the data stays internal (never on the view link) and out of
this public repo (data/ is gitignored).
"""
import argparse
import csv
import html
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PROJ = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(PROJ))
try:
    from dotenv import load_dotenv
    load_dotenv(PROJ / ".env", override=True)
except ModuleNotFoundError:
    pass
DATA = PROJ / "data" / "isa"
TEXT = DATA / "text"
LIST = DATA / "trials.csv"
BASE = "https://onlinedb.iasoybeans.com/onlinedb/"
UA = {"User-Agent": "Mozilla/5.0 (JSA yield-portal research)"}
FIRST_YEAR = 2005
PAUSE = 2.0                          # seconds between requests to ISA's server
LIST_COLUMNS = ["year", "landform", "district", "county", "crop", "trial_type", "trial_detail",
                "avg_response", "trial_id", "report_url"]


def _get(url, tries=3):
    for i in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120).read()
        except Exception as exc:
            # 404: a report the list links but ISA doesn't have (many 2006 "A"/"B" trials)
            gone = isinstance(exc, urllib.error.HTTPError) and exc.code == 404
            if gone or i == tries - 1:
                raise
            time.sleep(10 * (i + 1))


def fetch_list(last_year: int) -> list:
    """Every trial in the database, one year per request."""
    rows = []
    for year in range(FIRST_YEAR, last_year + 1):
        q = [("year[]", str(year)), ("crop[]", "all"), ("trialtype[]", "all"),
             ("trialdetail[]", "all"), ("landform[]", "all"), ("cropdistrict[]", "all"),
             ("watershed[]", "all"), ("county[]", "all"), ("btnSubmit", "Display Results")]
        page = _get(BASE + "?" + urllib.parse.urlencode(q)).decode("utf-8", "replace")
        n = 0
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S | re.I):
            cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)
            if len(cells) < 10:
                continue
            text = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in cells]
            link = re.search(r'href="([^"]+\.pdf)"', cells[9], re.I)
            rows.append(dict(zip(LIST_COLUMNS, text[:9] + [
                urllib.parse.urljoin(BASE, link.group(1)) if link else ""])))
            n += 1
        print(f"  {year}: {n} trials", flush=True)
        time.sleep(PAUSE)
    return rows


def fetch(last_year: int):
    import fitz
    TEXT.mkdir(parents=True, exist_ok=True)
    rows = fetch_list(last_year)
    with open(LIST, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, LIST_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    todo = [r for r in rows if r["report_url"] and not (TEXT / f"{r['trial_id']}.txt").exists()]
    print(f"{len(rows)} trials, {len(todo)} reports to fetch", flush=True)
    failed = []
    for i, r in enumerate(todo, 1):
        try:
            doc = fitz.open(stream=_get(r["report_url"]), filetype="pdf")
            text = "\f".join(p.get_text() for p in doc)
            (TEXT / f"{r['trial_id']}.txt").write_text(text, encoding="utf-8")
        except Exception as exc:
            failed.append((r["trial_id"], f"{type(exc).__name__}: {exc}"))
        if i % 100 == 0:
            print(f"  {i}/{len(todo)} fetched, {len(failed)} failed", flush=True)
        time.sleep(PAUSE)
    with open(DATA / "failed.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(failed)
    gone = sum("404" in why for _, why in failed)
    print(f"done: {len(todo) - len(failed)} fetched, {len(failed)} failed "
          f"({gone} not on ISA's server)", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["fetch", "load"])
    ap.add_argument("--target", choices=("sqlite", "snowflake"), default="sqlite")
    ap.add_argument("--last-year", type=int, default=time.localtime().tm_year - 1)
    args = ap.parse_args()
    if args.step == "fetch":
        fetch(args.last_year)
    else:
        os.environ["USE_SNOWFLAKE"] = "1" if args.target == "snowflake" else ""
        import isa_trials
        isa_trials.load_from_cache(LIST, TEXT, args.target)


if __name__ == "__main__":
    main()
