"""
Date the archive from the report emails, in bulk.

    python match_emails.py --target sqlite            # dry run: report only
    python match_emails.py --target sqlite --apply    # write it
    pythonw match_emails.py --target snowflake --auto # the scheduled task (no console)
    python match_emails.py --target snowflake --source graph [--auto]   # no Outlook: the droplet

Reads every Ag Trader Talk yield email, from Outlook's cache over COM (Inbox,
Deleted Items, Archive) or, with --source graph, from the whole mailbox on the
server through Microsoft Graph (graph_mail.py; needs the Mail.Read permission).
It parses each one exactly like the Add reports page, and for each report in it:
  date    archive has it, undated or dated later -> stamp this email's date,
          subject and Message-ID on that row
  dated   archive has it with this date or earlier -> nothing to do
  new     not in the archive -> add it, dated
  review  no crop or no state could be read -> stored without them (when
          writing), so it waits on Review & edit as "Needs crop/state"
Emails run oldest first, so the first report date wins; an email whose
Message-ID is already on a row is skipped, so re-running is safe. A colleague's
forward of one of the emails ("FW: YIELD: ...", how the 2024 ones arrived) is
read as the original: its subject, report and send time come from the forwarded
header block (parse_pdfs.unwrap_forward). An email without YIELD in its subject
is a conversation, not a report, and is skipped.

--auto is what the Windows scheduled task "Yield Portal - email sync" runs every
30 minutes on Kolten's PC: it writes, sends all output to logs/email_sync.log,
and records each run in EMAIL_SYNC_RUNS so Review & edit can show when the emails
were last checked. The task runs only while he's logged in (Outlook COM needs
his session); a missed run is caught up on the next one.

Outlook COM only sees classic Outlook's local cache: mail outside its Cached
Exchange window, or not synced yet, is invisible here (see the
reference_outlook_cache_window memory). Kolten reads mail in the new Outlook, so
classic Outlook's cache is only as fresh as its last sync. A report he archived
sits in the Archive folder, which syncs after the Inbox. When --auto has to start
Outlook itself, it waits for the cache to catch up first. Mail the cache can't
reach yet can be saved from Outlook as .msg files and passed with --msg.
"""
import argparse
import collections
import datetime as dt
import os
import pathlib
import socket
import subprocess
import sys
import time
import traceback

HERE = pathlib.Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env", override=True)
except ModuleNotFoundError:
    pass

LOG = HERE / "logs" / "email_sync.log"


def _to_log():
    """Unattended runs (pythonw, no console) write everything to logs/email_sync.log;
    a log past 1 MB is kept once as .old."""
    LOG.parent.mkdir(exist_ok=True)
    if LOG.exists() and LOG.stat().st_size > 1_000_000:
        LOG.replace(LOG.with_suffix(".log.old"))
    sys.stdout = sys.stderr = open(LOG, "a", encoding="utf-8", buffering=1)
    print(f"\n===== {dt.datetime.now():%Y-%m-%d %H:%M:%S} on {socket.gethostname()}")


if "--auto" in sys.argv:          # before Streamlit is imported: its warnings land in the log
    _to_log()

import pandas as pd  # noqa: E402

import data  # noqa: E402
import db  # noqa: E402
import parse_pdfs as P  # noqa: E402

SENDER = ('@SQL="http://schemas.microsoft.com/mapi/proptag/0x5D01001F" '
          "LIKE '%agtradertalk%'")       # Unicode sender tag: the filter that doesn't undercount
# Forwards come from colleagues, so they're found by subject (DASL LIKE only does
# prefix or substring) and kept only when unwrap_forward finds the original header.
FORWARDED = '@SQL="urn:schemas:httpmail:subject" LIKE \'%YIELD:%\''
MESSAGE_ID = "http://schemas.microsoft.com/mapi/proptag/0x1035001F"
FOLDERS = ["Inbox", "Deleted Items", "Archive"]


def _original(e):
    """An email from either source -> as first sent: a colleague's forward is
    unwrapped (its own replies quote the source's headers too, so only mail
    from someone else)."""
    e = dict(e, forwarded=False)
    if "agtradertalk" not in e["sender"]:
        fwd = P.unwrap_forward(e["body"])
        if fwd:
            e.update(subject=fwd[0], body=fwd[1], received=fwd[2].replace(second=0),
                     folder=f"{e['folder']} (forward)", forwarded=True)
    return e


def _mail(m, folder):
    """An Outlook item -> the email as first sent."""
    t = m.ReceivedTime                                  # wall-clock local time
    return _original({
        "folder": folder, "subject": m.Subject or "", "body": m.Body or "",
        "sender": (m.SenderEmailAddress or "").lower(),
        "received": dt.datetime(t.year, t.month, t.day, t.hour, t.minute),
        "msgid": m.PropertyAccessor.GetProperty(MESSAGE_ID)})


def read_graph():
    """The source's emails and colleagues' forwards of them, from every folder of
    the mailbox on the server (graph_mail.py)."""
    import graph_mail
    emails = [_original(e) for e in graph_mail.read_emails()]
    return [e for e in emails if "agtradertalk" in e["sender"] or e["forwarded"]]


def _outlook_running():
    """Is classic Outlook already open? Then its cache has been syncing all along."""
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq OUTLOOK.EXE", "/NH"],
                         capture_output=True, text=True,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    return "OUTLOOK.EXE" in out.stdout.upper()


def _let_cache_catch_up(root, quiet=45, limit=240):
    """Outlook was started for this run, with no window: give its cache time to sync
    before reading, until the folders' item counts hold still for `quiet` seconds
    (at most `limit`)."""
    def counts():
        out = []
        for name in FOLDERS:
            try:
                out.append(root.Folders[name].Items.Count)
            except Exception:
                out.append(None)
        return out
    t0 = changed = time.time()
    last = counts()
    while time.time() - t0 < limit and time.time() - changed < quiet:
        time.sleep(5)
        now = counts()
        if now != last:
            last, changed = now, time.time()
    print(f"Started Outlook for this run; gave its cache {time.time() - t0:.0f}s to sync")


def read_emails(catch_up=False):
    import win32com.client
    started_here = catch_up and not _outlook_running()
    ns = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
    root = ns.DefaultStore.GetRootFolder()
    if started_here:
        _let_cache_catch_up(root)
    out = {}
    for name in FOLDERS:
        for flt in (SENDER, FORWARDED):
            try:
                items = root.Folders[name].Items.Restrict(flt)
            except Exception:
                continue
            for m in items:
                try:
                    if m.Class != 43:                   # mail items only
                        continue
                    e = _mail(m, name)
                except Exception:
                    continue
                if flt is SENDER or e["forwarded"]:
                    out.setdefault(e["msgid"], e)
    return sorted(out.values(), key=lambda e: e["received"])


def read_msg_files(paths):
    """Saved .msg files (e.g. saved out of Outlook's server search) — for emails
    the local cache doesn't hold yet."""
    import win32com.client
    ns = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
    return [_mail(ns.OpenSharedItem(str(pathlib.Path(p).resolve())), "msg file")
            for p in paths]


def _desc(r):
    y = f"{r['yield_bpa']:g}" if r.get("yield_bpa") is not None else "-"
    return f"{r.get('crop') or '?':8} {r.get('state') or '??'} {str(r.get('location'))[:22]:22} {y:>6}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["sqlite", "snowflake"], required=True)
    ap.add_argument("--connection",
                    help="Snowflake profile in ~/.snowflake/connections.toml")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--auto", action="store_true",
                    help="unattended (the scheduled task): writes, logs to logs/email_sync.log "
                         "and records the run for the portal")
    ap.add_argument("--msg", nargs="*", default=[], help="also read these saved .msg files")
    ap.add_argument("--source", choices=["outlook", "graph"], default="outlook",
                    help="outlook: classic Outlook's cache over COM (Windows); graph: the "
                         "mailbox on the server via Microsoft Graph (graph_mail.py)")
    args = ap.parse_args()
    if args.source == "graph" and args.msg:
        ap.error("--msg reads files through Outlook; it doesn't go with --source graph")
    if not args.auto:
        return run(args)
    args.apply = True
    try:
        tally = run(args)
        db.record_sync_run(socket.gethostname(), emails=tally.get("emails", 0),
                           dated=tally.get("date", 0), added=tally.get("new", 0),
                           needs_review=tally.get("review", 0))
    except Exception as exc:
        traceback.print_exc()
        try:
            db.record_sync_run(socket.gethostname(), error=f"{type(exc).__name__}: {exc}")
        except Exception:
            traceback.print_exc()
        sys.exit(1)


def run(args):
    """One pass over the emails. -> the tally (emails, date, dated, new, review)."""
    if args.target == "snowflake":
        os.environ["USE_SNOWFLAKE"] = "1"
        if args.connection:
            os.environ["SNOWFLAKE_CONNECTION_NAME"] = args.connection
    else:
        os.environ.pop("USE_SNOWFLAKE", None)
    if db.use_snowflake() != (args.target == "snowflake"):
        sys.exit(f"Backend mismatch: asked {args.target}, got {db.backend_name()}.")
    print(f"{'APPLYING to' if args.apply else 'DRY RUN against'} {db.backend_name()}\n")

    archive = data.frame()
    applied = set(archive["email_id"].dropna())
    archive = archive[archive["status"] != "superseded"].reset_index(drop=True)
    if args.source == "graph":
        emails = read_graph()
    else:
        emails = read_emails(catch_up=args.auto)
        seen_ids = {e["msgid"] for e in emails}
        emails += [e for e in read_msg_files(args.msg) if e["msgid"] not in seen_ids]
    emails.sort(key=lambda e: e["received"])
    print(f"{len(emails)} Ag Trader Talk emails "
          f"({dict(collections.Counter(e['folder'] for e in emails))})\n")

    tally, review = collections.Counter(emails=len(emails)), []
    for e in emails:
        if e["msgid"] in applied:
            tally["email already applied"] += 1
            continue
        if not P.is_report_subject(e["subject"]):
            tally["not a report"] += 1
            print(f"{e['received']:%Y-%m-%d %H:%M}  {e['subject'][:70]}\n"
                  "      (skipped: not a report, no YIELD in the subject)")
            continue
        d = e["received"].date()
        rows = P.parse_email(e["subject"], e["body"], d.year, d)
        print(f"{e['received']:%Y-%m-%d %H:%M}  {e['subject'][:70]}")
        if not rows:
            print("      (no report text)")
        for r in rows:
            kind, h, _ = data.find_match(archive, r)
            if h and r.get("crop") not in data.CROPS:
                r["crop"] = archive.loc[archive["dedup_hash"] == h, "crop"].iloc[0]
            if h:
                hit = archive["dedup_hash"] == h
                old = archive.loc[hit, "date_reported"].iloc[0]
                if pd.notna(old) and old <= d:
                    act = "dated"
                else:
                    act = "date"
                    archive.loc[hit, ["date_reported", "email_subject", "email_id"]] = \
                        [d, e["subject"], e["msgid"]]
                    if args.apply:
                        db.set_reported(h, d, e["subject"], e["msgid"])
                print(f"      {act:6} {_desc(r)}  <- {data.describe(archive, h)}")
            elif r.get("crop") in data.CROPS and r.get("state"):
                act = "new"
                r["email_id"] = e["msgid"]
                new_row = {c: r.get(c) for c in archive.columns}
                archive = pd.concat([archive, pd.DataFrame([new_row])], ignore_index=True)
                if args.apply:
                    db.insert_new([r])
                print(f"      {act:6} {_desc(r)}")
            else:
                # no crop or no state: stored anyway, so it waits on Review & edit as
                # "Needs crop/state" instead of being lost in a log
                act = "review"
                review.append((e, r))
                r["email_id"] = e["msgid"]
                if args.apply:
                    db.insert_new([r])
                print(f"      {act:6} {_desc(r)}  ({r['raw_text'][:60]})")
            tally[act] += 1

    print("\nSUMMARY:", dict(tally))
    if review:
        print(f"\n{len(review)} report(s) need a crop or state"
              + (" — they wait on Review & edit:" if args.apply else ":"))
        for e, r in review:
            print(f"  {e['received']:%Y-%m-%d}  {e['subject'][:50]}  |  {r['raw_text'][:80]}")
    if not args.apply:
        print("\nDry run — nothing written. Re-run with --apply to write.")
    return tally


if __name__ == "__main__":
    main()
