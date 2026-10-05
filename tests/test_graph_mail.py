"""
Checks for graph_mail: an email's HTML must come out as the lines Outlook's
.Body gives, so the parser reads the same reports on the droplet as on the PC.

    python tests/test_graph_mail.py

The HTML below is MADE UP, in the shape the real emails take (Outlook/Word
markup, the mail filter's hidden preview and banner, list paragraphs). The
real-email comparison is in tests/test_parsing_local.py.
"""
import datetime as dt
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import graph_mail as G  # noqa: E402
import parse_pdfs as P  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


HTML = (
    "<html xmlns:o='urn:schemas-microsoft-com:office:office'><head><style>p {color: red}</style>"
    "</head><body><div style='display:none'>Logan Co IL 40 acres went 251 BPA</div>"
    "<table><tr><td>External (<a href='mailto:reports@agtradertalk.com'>reports@agtradertalk.com"
    "</a>)</td></tr><tr><td><a href='https://protection.inkyphishfence.com/report?id=1'>Safe</a> "
    "<a href='https://protection.inkyphishfence.com/report?id=2'>Spam</a></td></tr></table>"
    "<p class=MsoNormal>Logan Co IL 40 acres&nbsp;went 251 BPA dry.<o:p></o:p></p>"
    "<ul><li>Linn Co, MO: 220 bpa</li></ul>"
    "<p class=MsoListParagraph><![if !supportLists]><span>&#183;<span>&nbsp;&nbsp; </span></span>"
    "<![endif]>Greene Co, IL: 225 bpa</p>"
    "<p class=MsoNormal>Reports <a href='mailto:reports@agtradertalk.com'>reports@agtradertalk.com"
    "</a></p></body></html>")
text = G.html_to_text(HTML)
check("style text is dropped", "color: red" in text, False)
check("a link keeps its address, as in Outlook's .Body (the filter's banner is found by it)",
      "Safe <https://protection.inkyphishfence.com/report?id=1>" in text, True)
check("&nbsp; is a space", "40 acres went 251 BPA dry." in text, True)
check("a list item is its own line", "* Linn Co, MO: 220 bpa" in text.splitlines(), True)
rows = P.parse_email("YIELD: IL corn", text, 2026, dt.date(2026, 9, 30))
check("parsed: the preview before the banner and the signature drop out; each report once",
      [(r["location"], r["state"], r["yield_bpa"]) for r in rows],
      [("Logan Co", "IL", 251), ("Linn Co", "MO", 220), ("Greene Co", "IL", 225)])
check("Graph's UTC time -> Central to the minute",
      G._local("2026-10-02T14:14:30Z"), dt.datetime(2026, 10, 2, 9, 14))
check("plain text stays plain", G.html_to_text("Sac Co, IA: 230 bpa"), "Sac Co, IA: 230 bpa")

if failures:
    print(f"FAILED {len(failures)}:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("All Graph mail checks pass.")
