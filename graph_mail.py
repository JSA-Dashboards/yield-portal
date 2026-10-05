"""
The report emails from Microsoft Graph instead of Outlook, so the email sync can
run on the droplet (Linux, no Outlook).

App-only (client-credentials) OAuth, like the basis tracker's sender. The app
needs the **Mail.Read application permission**, admin-consented, and ideally an
ApplicationAccessPolicy limiting it to the one mailbox it reads. Until IT grants
that, its token carries only Mail.Send and `read_emails` stops with a clear
error instead of a bare 403.

    GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET   the app
    YIELD_MAILBOX                                           the mailbox to read

Reads each message's stored HTML (what a saved .msg holds) and turns it into
text laid out the way Outlook's .Body lays it out: paragraphs, <br> and list
items as lines, links as "text <url>" (the mail filter's banner is found by its
links). The email parser then sees the lines it sees on the PC. tests/test_parsing_local.py
checks this against saved emails.
"""
import base64
import datetime as dt
import html
import json
import os
import re
from html.parser import HTMLParser

GRAPH = "https://graph.microsoft.com/v1.0"
SEARCHES = ['"from:agtradertalk.com"',      # the source's own emails
            '"agtradertalk.com"']           # + a colleague's forward that quotes his header
_BLOCK = {"p", "div", "br", "li", "tr", "table", "ul", "ol", "blockquote", "pre",
          "h1", "h2", "h3", "h4", "h5", "h6", "hr"}
_SKIP = {"style", "script", "head", "title", "xml"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines, self.cur, self.skip, self.links = [], [], 0, []

    def _break(self):
        line = re.sub(r"[ \t\r\n\f]+", " ", "".join(self.cur)).strip()
        self.lines.append(line)
        self.cur = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self.skip += 1
        elif tag in _BLOCK:
            self._break()
            if tag == "li":
                self.cur.append("* ")
        elif tag == "a":
            self.links.append((dict(attrs).get("href") or "", len("".join(self.cur))))
        elif tag == "td":
            self.cur.append(" ")

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK:
            self._break()

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCK:
            self._break()
        elif tag == "a" and self.links:
            href, start = self.links.pop()
            text = "".join(self.cur)[start:].strip()
            if href and not href.startswith("#") and href.strip() != text:
                self.cur.append(f" <{href}>")         # Outlook's .Body: "Safe <https://...>"

    def handle_data(self, data):
        if not self.skip:
            self.cur.append(data.replace("\xa0", " "))

    def text(self):
        self._break()
        return "\n".join(self.lines)


def html_to_text(content: str) -> str:
    """An email's HTML body as text, one block per line (blank lines kept)."""
    p = _Text()
    p.feed(content or "")
    p.close()
    return p.text()


# --- Graph ---------------------------------------------------------------------------
def _config():
    cfg = {k: os.environ.get(k, "").strip() for k in
           ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET", "YIELD_MAILBOX")}
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        raise RuntimeError("Graph email reading needs " + ", ".join(missing))
    return cfg


def _token(cfg) -> str:
    import msal
    app = msal.ConfidentialClientApplication(
        cfg["GRAPH_CLIENT_ID"], client_credential=cfg["GRAPH_CLIENT_SECRET"],
        authority=f"https://login.microsoftonline.com/{cfg['GRAPH_TENANT_ID']}")
    tok = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in tok:
        raise RuntimeError(f"Graph sign-in failed: {tok.get('error')}: "
                           f"{(tok.get('error_description') or '')[:200]}")
    payload = tok["access_token"].split(".")[1]
    roles = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("roles", [])
    if not {"Mail.Read", "Mail.ReadWrite"} & set(roles):
        raise RuntimeError(f"The Graph app can't read mail yet (its permissions: {roles}). "
                           "IT has to grant it Mail.Read (Application), admin-consented.")
    return tok["access_token"]


def _local(stamp: str) -> dt.datetime:
    """Graph's UTC receivedDateTime -> Central wall-clock time to the minute, as
    Outlook on the PC reports it (the droplet's own clock is UTC)."""
    t = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    try:
        from zoneinfo import ZoneInfo
        t = t.astimezone(ZoneInfo("America/Chicago"))
    except Exception:                       # no tz database (bare Windows): the machine's zone
        t = t.astimezone()
    return t.replace(tzinfo=None, second=0, microsecond=0)


def _pages(session, url):
    while url:
        r = session.get(url, timeout=60)
        r.raise_for_status()
        body = r.json()
        yield from body.get("value", [])
        url = body.get("@odata.nextLink")


def read_emails(mailbox=None):
    """The mailbox's emails from or quoting the source, as the matcher's dicts:
    folder, subject, body (text), sender, received (Central, to the minute) and
    msgid (Internet Message-ID, as Outlook gives it). Every folder, so mail
    archived from any Outlook counts."""
    import requests
    cfg = _config()
    mailbox = mailbox or cfg["YIELD_MAILBOX"]
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {_token(cfg)}"
    base = f"{GRAPH}/users/{mailbox}"
    folders = {f["id"]: f["displayName"] for f in
               _pages(s, f"{base}/mailFolders?$top=100&$select=id,displayName")}
    out = {}
    fields = "subject,body,receivedDateTime,internetMessageId,from,parentFolderId"
    for q in SEARCHES:
        for m in _pages(s, f"{base}/messages?$search={q}&$top=100&$select={fields}"):
            msgid = m.get("internetMessageId")
            if not msgid or msgid in out:
                continue
            b = m.get("body") or {}
            text = (html_to_text(b.get("content", "")) if b.get("contentType") == "html"
                    else html.unescape(b.get("content", "")))
            out[msgid] = {
                "folder": folders.get(m.get("parentFolderId"), "Mailbox"),
                "subject": m.get("subject") or "", "body": text,
                "sender": ((m.get("from") or {}).get("emailAddress") or {}).get("address", "").lower(),
                "received": _local(m["receivedDateTime"]), "msgid": msgid}
    return sorted(out.values(), key=lambda e: e["received"])
