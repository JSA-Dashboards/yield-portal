"""
Variety-trial watcher: emails when a state university posts a new season's corn or
soybean variety-trial results, so the Variety trials tables can be refreshed by hand
(each state's parser in the illinois-corn-trials project has to be run and checked
against the university's printed averages; formats change too often to reload
unattended). Kolten, 2026-10-22.

Each source is either a page that links the season's results (a regex with {Y}) or a
file that only exists once posted. Every run also checks the season before: a source
that can't see last season has moved, and the email says so instead of the watcher
going quietly blind.

    python variety_watch.py                # print the status; nothing is sent or saved
    python variety_watch.py --notify       # email new seasons (once each) to VARIETY_WATCH_TO
"""
import argparse
import datetime as dt
import html
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env", override=True)
except ModuleNotFoundError:
    pass

STATE = HERE / "logs" / "variety_watch.json"
UA = {"User-Agent": "Mozilla/5.0 (JSA variety-trial watcher)"}

# (label, kind, url, pattern): kind "page" = the url's HTML matches pattern; kind
# "file" = the url (with {Y}) returns a non-HTML file.
# Programs that stopped posting at their source (checked 2026-10-22): still watched in
# case they come back, but shown as "last posted N" rather than "source moved".
ENDED = {"Nebraska corn": 2023, "Nebraska soybeans": 2023, "Wisconsin corn": 2023,
         "Michigan corn": 2022}
SOURCES = [
    ("Illinois corn", "page", "https://vt.cropsci.illinois.edu/corn/",
     r"uploads/\d{{4}}/\d\d/[^\"']*{Y}[^\"']*\.xlsx"),
    ("Illinois soybeans", "page", "https://vt.cropsci.illinois.edu/soybean/",
     r"uploads/\d{{4}}/\d\d/[^\"']*{Y}[^\"']*\.xlsx"),
    ("Iowa corn", "file", "https://www.croptesting.iastate.edu/Corn/Docs/{Y}_ISU_CornDistrictResults.xlsx", None),
    ("Iowa soybeans", "file", "https://www.croptesting.iastate.edu/Soybean/Docs/{Y}_ISU_SoybeanDistrictResults.xlsx", None),
    ("Ohio corn", "file", "https://ohiocroptest.cfaes.osu.edu/corntrials/LSW{Y}.xlsx", None),
    ("Missouri corn", "page", "https://varietytesting.missouri.edu/archive.htm",
     r"archive/{Y}[-_ ]?corn[^\"']*\.pdf"),
    ("Missouri soybeans", "page", "https://varietytesting.missouri.edu/archive.htm",
     r"archive/{Y}[-_ ]?soy[^\"']*\.pdf"),
    ("Nebraska corn", "page", "https://cropwatch.unl.edu/variety-testing/corn/archive/",
     r"files/media/file/{Y}-[^\"']*\.pdf"),
    ("Nebraska soybeans", "page", "https://cropwatch.unl.edu/variety-testing/soybean/archive/",
     r"files/media/file/{Y}-[^\"']*\.pdf"),
    ("Wisconsin corn", "page", "https://corn.agronomy.wisc.edu/HT/Default.aspx", r"href=\"{Y}/"),
    ("Wisconsin soybeans", "page", "https://badgercropnetwork.com/research/soybeans/variety-trial-results/",
     r"{Y}-WI-Soybean-Variety-Performance-Trials[^\"']*\.xlsx"),     # not the entry forms
    ("Michigan soybeans", "page", "https://msu-prod.dotcmscloud.com/varietytrials/soybean/",
     r"/resources/{Y}-soybean-variety-trial-report"),
    ("Michigan corn", "page", "https://msu-prod.dotcmscloud.com/varietytrials/corn/",
     r"/resources/{Y}-[^\"']*corn"),
]


def season(today):
    """The season whose results are due: this year's from August on, else last year's."""
    return today.year if today.month >= 8 else today.year - 1


def _get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def posted(kind, url, pattern, year, cache):
    """True / False, or an error string when the source couldn't be read."""
    try:
        if kind == "file":
            status, ctype, body = _get(url.format(Y=year))
            return status == 200 and "html" not in ctype.lower() and len(body) > 2000
        if url not in cache:
            cache[url] = _get(url)[2].decode("utf-8", "replace")
        return bool(re.search(pattern.format(Y=year), cache[url], re.I))
    except urllib.error.HTTPError as exc:
        return False if exc.code == 404 else f"HTTP {exc.code}"
    except Exception as exc:
        return f"{type(exc).__name__}: {str(exc)[:80]}"


def check(today):
    y = season(today)
    cache, rows = {}, []
    for label, kind, url, pattern in SOURCES:
        rows.append({"source": label, "season": y, "url": url.format(Y=y),
                     "now": posted(kind, url, pattern, y, cache),
                     "last": posted(kind, url, pattern, y - 1, cache)})
    return y, rows


def _last(r):
    if r["last"] is True:
        return "yes"
    if r["source"] in ENDED:
        return f"last posted {ENDED[r['source']]}"
    return "NO: the source may have moved" if r["last"] is False else html.escape(str(r["last"]))


def email_html(y, new, rows):
    e = html.escape
    lines = "".join(f"<li><b>{e(r['source'])} {y}</b>: <a href=\"{e(r['url'])}\">{e(r['url'])}</a></li>"
                    for r in new)
    status = "".join(
        f"<tr><td>{e(r['source'])}</td><td>{'posted' if r['now'] is True else 'not yet' if r['now'] is False else e(str(r['now']))}"
        f"</td><td>{_last(r)}</td></tr>"
        for r in rows)
    return (f"<div style='font-family:Segoe UI,Arial,sans-serif;font-size:13px;color:#32373c'>"
            f"<p>New {y} variety-trial results are posted:</p><ul>{lines}</ul>"
            "<p>Ask Claude to refresh the Variety trials for these states: download, parse, check "
            "against the printed averages, rebuild and reload.</p>"
            f"<table cellpadding='4' style='border-collapse:collapse;font-size:12px'>"
            f"<tr><th align='left'>Source</th><th align='left'>{y}</th>"
            f"<th align='left'>{y - 1} still visible</th></tr>{status}</table></div>")


def main():
    ap = argparse.ArgumentParser(description="Watch the state variety-trial sources for a new season.")
    ap.add_argument("--notify", action="store_true", help="email new seasons, once each")
    ap.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    args = ap.parse_args()
    y, rows = check(args.today)
    print(f"===== variety watch {dt.datetime.now():%Y-%m-%d %H:%M} · season {y}")
    for r in rows:
        print(f"  {r['source']:<20} {y}: {r['now']!s:<8} {y - 1} visible: {_last(r)}")
    seen = json.loads(STATE.read_text()) if STATE.exists() else {}
    new = [r for r in rows if r["now"] is True and f"{r['source']} {y}" not in seen]
    print(f"new since last notice: {[r['source'] for r in new] or 'none'}")
    if not args.notify or not new:
        return
    to = os.environ.get("VARIETY_WATCH_TO", "").strip() or os.environ.get("WEEKLY_EMAIL_REPLY_TO", "").strip()
    if not to:
        sys.exit("No recipient: set VARIETY_WATCH_TO in .env")
    import weekly_email                       # Graph sendMail as the shared mailbox
    subject = f"Variety trials: {y} results posted ({', '.join(r['source'] for r in new)})"
    weekly_email.send_graph(to, subject, email_html(y, new, rows), {})
    stamp = dt.date.today().isoformat()
    seen.update({f"{r['source']} {y}": stamp for r in new})
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(seen, indent=1))


if __name__ == "__main__":
    main()
