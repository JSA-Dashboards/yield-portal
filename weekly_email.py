"""
The weekly yield-report email (Tuesday mornings, to the JSA group).

    python weekly_email.py --preview                     # logs/weekly_preview.html, nothing sent
    python weekly_email.py --to someone@jpsi.com         # send it now (a test)
    python weekly_email.py --scheduled --via graph       # the Droplet's cron job: once a week, logged

For the current crop year, per crop, the Explore headline tiles (data.headline,
with % changes) and two charts (reported yields by crop year; average by state,
this year against last). Then the season's report text laid out like the yield
PDF (report_text.py), with the reports dated in the past week highlighted in
yellow and listed in a box at the top. Tiles and charts count what Explore
counts (clean + approved, corn without silage); the text holds every report
except superseded and excluded ones, as Report text does.

"The past week" is the seven days before the send day, Tuesday to Monday for a
Tuesday send, so each report date lands in exactly one email.

Two ways to send:
  --via graph    Microsoft Graph sendMail, app-only, as the shared mailbox in
                 GRAPH_SENDER (the basis tracker's app, which IT gave Mail.Send).
                 This is how the Droplet sends it. The GRAPH_* settings come
                 from the environment, or from the .env named by GRAPH_ENV_FILE
                 (on the Droplet, the basis tracker's: one copy of the secret to
                 rotate). Shown as WEEKLY_EMAIL_FROM_NAME; replies go to
                 WEEKLY_EMAIL_REPLY_TO.
  --via outlook  classic Outlook over COM, as Kolten (the PC). .Send() only
                 queues it, and cached Exchange mode sends on Outlook's own
                 cycle, so the script waits up to 35 minutes for the Outbox to
                 drain.
--scheduled sends at most once per ISO week, checked against the WEEKLY_EMAILS
table, so a catch-up run, or a second machine left scheduled, can't send it
twice. Its recipient is --to or WEEKLY_EMAIL_TO; addresses stay out of this
public repo.
"""
import argparse
import base64
import datetime as dt
import html
import io
import os
import pathlib
import socket
import sys
import tempfile
import time
import traceback

HERE = pathlib.Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env", override=True)
except ModuleNotFoundError:
    pass

LOG = HERE / "logs" / "weekly_email.log"

if "--scheduled" in sys.argv:          # before Streamlit is imported: its warnings land in the log
    LOG.parent.mkdir(exist_ok=True)
    sys.stdout = sys.stderr = open(LOG, "a", encoding="utf-8", buffering=1)
    print(f"\n===== {dt.datetime.now():%Y-%m-%d %H:%M:%S} on {socket.gethostname()}")

os.environ["USE_SNOWFLAKE"] = "1"      # the archive of record

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402

import data  # noqa: E402
import db  # noqa: E402
import report_text as RT  # noqa: E402
import summary as S  # noqa: E402
# the summary's pieces (tiles, charts, the new box) live in summary.py, shared with the PDF
from summary import (BLUE, DARK, FONT, GRAY, NEW_BG, _signed, box_chart,  # noqa: E402,F401
                     counted, state_chart, state_changes, tiles_html)

PR_ATTACH_CONTENT_ID = "http://schemas.microsoft.com/mapi/proptag/0x3712001F"
PR_ATTACHMENT_HIDDEN = "http://schemas.microsoft.com/mapi/proptag/0x7FFE000B"
e = html.escape


# --- what goes in ---------------------------------------------------------------
def send_day(today: dt.date) -> dt.date:
    """The Tuesday this email belongs to: today if it's Tuesday, else the last one."""
    return today - dt.timedelta(days=(today.weekday() - 1) % 7)


def week(day: dt.date):
    """The seven days before the send day: (first, last)."""
    return day - dt.timedelta(days=7), day - dt.timedelta(days=1)


def _in(d, lo, hi):
    return isinstance(d, dt.date) and lo <= d <= hi


def load():
    """Live reports (superseded ones dropped), typed and checked."""
    assert db.use_snowflake(), db.backend_name()
    df = data.frame()
    return df[df["status"] != "superseded"].reset_index(drop=True)


def text_html(df, lo, hi):
    # a report the parser couldn't give a crop (waiting on review) still shows, last
    df = df.assign(crop=df["crop"].where(df["crop"].isin(data.CROPS), "Crop not given"))
    out = []
    for crop, states in RT.sections(RT.ordered(df)):
        out.append(f'<h3 style="font-family:{FONT};font-size:15px;color:{BLUE};'
                   f'margin:18px 0 4px 0">{e(str(crop))}</h3>')
        for name, rows in states:
            out.append(f'<div style="font-size:13px;font-weight:600;color:{DARK};'
                       f'margin:10px 0 2px 0">{e(name)} '
                       f'<span style="color:{GRAY};font-weight:400">· {len(rows)}</span></div>')
            trs = []
            for r in rows.itertuples():
                label, body = RT.label_and_body(r.raw_text, r.location, r.state)
                bg = f"background-color:{NEW_BG};" if _in(r.date_reported, lo, hi) else ""
                t = RT.source_tag(r.source, r.source_file)
                tag = (f' <span style="color:{BLUE if r.source == "JSA" else "#b45309"};'
                       f'font-size:11px;font-weight:600">{e(t)}</span>' if t else "")
                trs.append(
                    f'<tr><td valign="top" style="width:52px;padding:3px 6px;font-size:12px;'
                    f'color:{GRAY};white-space:nowrap">{RT.day_label(r.date_reported)}</td>'
                    f'<td valign="top" style="padding:3px 6px;font-size:13px;color:{DARK};'
                    f'line-height:1.4;{bg}">'
                    + (f"<b>{e(label)}</b> " if label else "") + e(body) + tag + "</td></tr>")
            out.append('<table role="presentation" width="100%" cellspacing="0" '
                       f'cellpadding="0">{"".join(trs)}</table>')
    return "".join(out)


# --- the email ------------------------------------------------------------------------
def build(today: dt.date):
    """-> (subject, html with cid: images, {cid: png bytes}, counts)."""
    df = load()
    day = send_day(today)
    lo, hi = week(day)
    year = int(df["crop_year"].max())

    season = df[(df["crop_year"] == year) & (df["status"] != "excluded")]
    new = season[season["date_reported"].map(lambda d: _in(d, lo, hi))]
    parts, images = S.crop_sections(df, year, new)

    portal = os.environ.get("YIELD_PORTAL_URL", "").strip()
    footer = S.footnote(year) + " Report text holds every report of the season."
    if portal:
        footer += f' <a href="{e(portal)}" style="color:{BLUE}">Open the Yield Portal</a>.'
    stamp = f"{day:%A}, {day:%B} {day.day}, {day.year}"
    body = (
        f'<div style="font-family:{FONT};color:{DARK};background:#ffffff">'
        '<table role="presentation" width="720" align="center" cellspacing="0" cellpadding="0" '
        f'style="width:720px;font-family:{FONT}">'
        f'<tr><td style="background-color:{DARK};padding:14px 18px">'
        '<div style="font-size:11px;letter-spacing:1.5px;color:#cfd4da">JOHN STEWART &amp; '
        "ASSOCIATES</div>"
        '<div style="font-size:20px;font-weight:600;color:#ffffff;margin-top:2px">'
        f"Weekly yield reports · {year} harvest</div>"
        f'<div style="font-size:12px;color:#cfd4da;margin-top:2px">{stamp}</div></td></tr>'
        '<tr><td style="padding:14px 18px 6px 18px">'
        + S.INTRO + S.new_box(new, lo, hi) + parts
        + f'<h2 style="font-family:{FONT};font-size:17px;color:{BLUE};margin:26px 0 0 0">'
        f"Report text · {year}</h2>"
        f'<p style="margin:2px 0 0 0;font-size:12px;color:{GRAY}">Crop, then state, each report '
        "in its own words with the date it was reported. Yellow: dated in the past week.</p>"
        + text_html(season, lo, hi)
        + f'<p style="margin:22px 0 8px 0;font-size:11px;color:{GRAY}">{footer}</p>'
        "</td></tr></table></div>")
    subject = (f"Weekly yield reports · {day:%b} {day.day}, {day.year}"
               + (f" · {len(new)} new" if len(new) else ""))
    counts = {"year": year, "week": f"{lo} to {hi}", "season_reports": len(season),
              "new": len(new), "images": len(images)}
    return subject, body, images, counts


def preview_html(body, images):
    """The email as a file a browser can open: images inline as data URIs."""
    for cid, png in images.items():
        body = body.replace(f"cid:{cid}", "data:image/png;base64," + base64.b64encode(png).decode())
    return f"<!doctype html><html><head><meta charset='utf-8'></head><body>{body}</body></html>"


# --- sending ---------------------------------------------------------------------------
def _graph_cfg():
    """The sending app (Mail.Send): GRAPH_* from the environment, else from the
    .env that GRAPH_ENV_FILE names (graph_mail.setting)."""
    from graph_mail import setting
    cfg = {k: setting(k) for k in
           ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET", "GRAPH_SENDER")}
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        raise RuntimeError("Sending through Graph needs " + ", ".join(missing))
    return cfg


def send_graph(to, subject, body, images, on_queued=None):
    """Microsoft Graph sendMail as the shared mailbox, images inline by Content-ID.
    Graph takes it straight away (202), so there's no Outbox to wait on. -> True."""
    import msal
    import requests
    cfg = _graph_cfg()
    app = msal.ConfidentialClientApplication(
        cfg["GRAPH_CLIENT_ID"], client_credential=cfg["GRAPH_CLIENT_SECRET"],
        authority=f"https://login.microsoftonline.com/{cfg['GRAPH_TENANT_ID']}")
    tok = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in tok:
        raise RuntimeError(f"Graph sign-in failed: {tok.get('error_description') or tok.get('error')}")
    sender = cfg["GRAPH_SENDER"]
    message = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": body},
        "toRecipients": [{"emailAddress": {"address": a.strip()}} for a in to.split(",") if a.strip()],
        # the shared mailbox's directory name would show otherwise
        "from": {"emailAddress": {"address": sender,
                                  "name": os.environ.get("WEEKLY_EMAIL_FROM_NAME") or "JSA Yield Reports"}},
        "attachments": [{"@odata.type": "#microsoft.graph.fileAttachment", "name": f"{cid}.png",
                         "contentType": "image/png", "contentId": cid, "isInline": True,
                         "contentBytes": base64.b64encode(png).decode("ascii")}
                        for cid, png in images.items()],
    }
    reply_to = os.environ.get("WEEKLY_EMAIL_REPLY_TO", "").strip()
    if reply_to:
        message["replyTo"] = [{"emailAddress": {"address": reply_to}}]
    r = requests.post(f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail",
                      headers={"Authorization": f"Bearer {tok['access_token']}"},
                      json={"message": message, "saveToSentItems": True}, timeout=60)
    if r.status_code not in (200, 202):
        raise RuntimeError(f"Graph sendMail failed [{r.status_code}]: {r.text[:300]}")
    print(f"Sent through Graph as {sender}: '{subject}' -> {to}")
    if on_queued:
        on_queued()
    return True


def send(to, subject, body, images, wait_minutes=35, on_queued=None):
    """Queue it in classic Outlook, images embedded, then wait for the Outbox to drain.
    `on_queued` runs once Outlook has taken it. -> True once sent, False if it's
    still queued after `wait_minutes`."""
    import win32com.client
    app = win32com.client.Dispatch("Outlook.Application")
    ns = app.GetNamespace("MAPI")
    mail = app.CreateItem(0)
    mail.To = to
    mail.Subject = subject
    with tempfile.TemporaryDirectory() as tmp:
        for cid, png in images.items():
            path = pathlib.Path(tmp) / f"{cid}.png"
            path.write_bytes(png)
            att = mail.Attachments.Add(str(path), 1, 0)          # 1 = olByValue
            att.PropertyAccessor.SetProperty(PR_ATTACH_CONTENT_ID, cid)
            att.PropertyAccessor.SetProperty(PR_ATTACHMENT_HIDDEN, True)
        mail.HTMLBody = body                                       # after the images: cid: resolves
        mail.Send()
    print(f"Queued '{subject}' -> {to}")
    if on_queued:
        on_queued()
    try:
        ns.SendAndReceive(False)
    except Exception:
        pass
    outbox = ns.GetDefaultFolder(4)
    t0 = time.time()
    while time.time() - t0 < wait_minutes * 60:
        time.sleep(15)
        waiting = False
        for item in outbox.Items:
            try:
                waiting |= item.Subject == subject
            except Exception:
                continue
        if not waiting:
            print(f"Sent after {time.time() - t0:.0f}s")
            return True
    print(f"Still in the Outbox after {wait_minutes} min: Outlook sends it on its next "
          "send/receive cycle.")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", nargs="?", const=str(HERE / "logs" / "weekly_preview.html"),
                    help="write the email to this HTML file (default logs/weekly_preview.html); "
                         "nothing is sent")
    ap.add_argument("--to", help="send it to this address now")
    ap.add_argument("--scheduled", action="store_true",
                    help="the scheduled job: send to --to (else WEEKLY_EMAIL_TO) at most once "
                         "per ISO week, logged")
    ap.add_argument("--via", choices=["outlook", "graph"], default="outlook",
                    help="outlook: classic Outlook over COM (the PC); graph: Microsoft Graph as "
                         "GRAPH_SENDER (the Droplet)")
    ap.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today(),
                    help="pretend it's this day (YYYY-MM-DD), for previews")
    args = ap.parse_args()
    to = args.to or (os.environ.get("WEEKLY_EMAIL_TO", "").strip() if args.scheduled else None)
    if not args.preview and not to:
        ap.error("give --preview, or --to an address (--scheduled also reads WEEKLY_EMAIL_TO)")

    day = send_day(args.today)
    key = f"{day.isocalendar().year}-W{day.isocalendar().week:02d}"
    if args.scheduled:
        prior = db.weekly_email_sent(key)
        if prior:
            print(f"Already sent for {key} ({prior['sent_at']} UTC from {prior['host']} via "
                  f"{prior['via']}); nothing to do.")
            return

    subject, body, images, counts = build(args.today)
    print(subject, counts)
    if args.preview:
        out = pathlib.Path(args.preview)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(preview_html(body, images), encoding="utf-8")
        print(f"Preview written to {out}")
    if to:
        def record():                 # once it's gone: nothing sends this week's again
            db.record_weekly_email(key, socket.gethostname(), to, args.via)
        sender = send_graph if args.via == "graph" else send
        ok = sender(to, subject, body, images, on_queued=record if args.scheduled else None)
        if not ok:
            sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
