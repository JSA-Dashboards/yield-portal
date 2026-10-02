"""
Date the archive from the report emails, in bulk.

    python match_emails.py --target sqlite            # dry run: report only
    python match_emails.py --target sqlite --apply    # write it

Reads every Ag Trader Talk yield email Outlook has cached (Inbox, Deleted Items, Archive)
over COM, parses each one exactly like the Add reports page, and for each report
in it:
  date    archive has it, undated or dated later -> stamp this email's date,
          subject and Message-ID on that row
  dated   archive has it with this date or earlier -> nothing to do
  new     not in the archive -> add it, dated
  review  no crop or no state could be read -> listed for Add reports instead
          of being guessed
Emails run oldest first, so the first report date wins; an email whose
Message-ID is already on a row is skipped, so re-running is safe.

Outlook COM only sees its Cached Exchange window — mail older than that, or not
yet synced, is invisible here (see the reference_outlook_cache_window memory).
"""
import argparse
import collections
import datetime as dt
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env", override=True)
except ModuleNotFoundError:
    pass

import pandas as pd  # noqa: E402

import data  # noqa: E402
import db  # noqa: E402
import parse_pdfs as P  # noqa: E402

SENDER = ('@SQL="http://schemas.microsoft.com/mapi/proptag/0x5D01001F" '
          "LIKE '%agtradertalk%'")       # Unicode sender tag: the filter that doesn't undercount
MESSAGE_ID = "http://schemas.microsoft.com/mapi/proptag/0x1035001F"
FOLDERS = ["Inbox", "Deleted Items", "Archive"]


def read_emails():
    import win32com.client
    ns = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
    root = ns.DefaultStore.GetRootFolder()
    out = []
    for name in FOLDERS:
        try:
            items = root.Folders[name].Items.Restrict(SENDER)
        except Exception:
            continue
        for m in items:
            try:
                if m.Class != 43:                       # mail items only
                    continue
                t = m.ReceivedTime                      # wall-clock local time
                out.append({
                    "folder": name, "subject": m.Subject or "", "body": m.Body or "",
                    "received": dt.datetime(t.year, t.month, t.day, t.hour, t.minute),
                    "msgid": m.PropertyAccessor.GetProperty(MESSAGE_ID),
                })
            except Exception:
                continue
    out.sort(key=lambda e: e["received"])
    return out


def read_msg_files(paths):
    """Saved .msg files (e.g. dragged out of Outlook) — for emails the local
    cache doesn't hold yet."""
    import win32com.client
    ns = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
    out = []
    for p in paths:
        m = ns.OpenSharedItem(str(pathlib.Path(p).resolve()))
        t = m.ReceivedTime
        out.append({"folder": "msg file", "subject": m.Subject or "", "body": m.Body or "",
                    "received": dt.datetime(t.year, t.month, t.day, t.hour, t.minute),
                    "msgid": m.PropertyAccessor.GetProperty(MESSAGE_ID)})
    return out


def _desc(r):
    y = f"{r['yield_bpa']:g}" if r.get("yield_bpa") is not None else "-"
    return f"{r.get('crop') or '?':8} {r.get('state') or '??'} {str(r.get('location'))[:22]:22} {y:>6}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["sqlite", "snowflake"], required=True)
    ap.add_argument("--connection",
                    help="Snowflake profile in ~/.snowflake/connections.toml")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--msg", nargs="*", default=[], help="also read these saved .msg files")
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

    archive = data.frame()
    applied = set(archive["email_id"].dropna())
    emails = read_emails()
    seen_ids = {e["msgid"] for e in emails}
    emails += [e for e in read_msg_files(args.msg) if e["msgid"] not in seen_ids]
    emails.sort(key=lambda e: e["received"])
    print(f"{len(emails)} Ag Trader Talk emails "
          f"({dict(collections.Counter(e['folder'] for e in emails))})\n")

    tally, review = collections.Counter(), []
    for e in emails:
        if e["msgid"] in applied:
            tally["email already applied"] += 1
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
                act = "review"
                review.append((e, r))
                print(f"      {act:6} {_desc(r)}  ({r['raw_text'][:60]})")
            tally[act] += 1

    print("\nSUMMARY:", dict(tally))
    if review:
        print(f"\n{len(review)} report(s) need a crop or state — paste these on Add reports:")
        for e, r in review:
            print(f"  {e['received']:%Y-%m-%d}  {e['subject'][:50]}  |  {r['raw_text'][:80]}")
    if not args.apply:
        print("\nDry run — nothing written. Re-run with --apply to write.")


if __name__ == "__main__":
    main()
