"""
Checks for the Report text page's layout helpers and its Word / PDF downloads.

    python tests/test_report_text.py

The reports below are MADE UP (the repo is public).
"""
import datetime as dt
import io
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import pandas as pd  # noqa: E402

import report_text as RT  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# --- the place label ---------------------------------------------------------------
check("label up to the colon",
      RT.label_and_body("Adams Co, IN: 248 bpa, 18% moisture.", "Adams Co", "IN"),
      ("Adams Co, IN:", "248 bpa, 18% moisture."))
check("no colon nearby: just the place",
      RT.label_and_body("Story Co IA 3 fields went 61-64 bpa", "Story Co", "IA"),
      ("Story Co", "IA 3 fields went 61-64 bpa"))
check("place only in the email subject: put in front",
      RT.label_and_body("80 acres went 96 bu/acre, a record.", "Clark Co", "IL"),
      ("Clark Co, IL:", "80 acres went 96 bu/acre, a record."))
check("place named later: left as written",
      RT.label_and_body("Beans in western Clark Co ran 70.", "Clark Co", "IL"),
      ("", "Beans in western Clark Co ran 70."))
check("no place", RT.label_and_body("Delta: beans averaged 70.", None, "MS"),
      ("", "Delta: beans averaged 70."))
check("NaN place", RT.label_and_body("Delta: beans averaged 70.", float("nan"), "MS"),
      ("", "Delta: beans averaged 70."))

# --- Markdown: report text can't turn into formatting ------------------------------
md = RT.md_report("Polk Co, IA: ~10% under LY, $4 corn, field #2 [dry] *ugh*", "Polk Co", "IA")
check("markdown escaped", md,
      r"**Polk Co, IA:** \~10% under LY, \$4 corn, field \#2 \[dry\] \*ugh\*")
check("search term highlighted in label and text",
      RT.md_report("Polk Co, IA: rust in Polk too", "Polk Co", "IA", "polk"),
      "**:yellow-background[Polk] Co, IA:** rust in :yellow-background[Polk] too")
check("regex characters in a search are literal",
      RT.md_report("Polk Co, IA: 200+ bpa", "Polk Co", "IA", "200+"),
      "**Polk Co, IA:** :yellow-background[200+] bpa")

# --- dates and order -----------------------------------------------------------------
check("day label", RT.day_label(dt.date(2026, 9, 4)), "Sep 4")
check("no date", [RT.day_label(v) for v in (None, float("nan"), pd.NaT)], ["", "", ""])
check("state names", [RT.state_name(s) for s in ("IA", "ND", None)],
      ["Iowa", "North Dakota", "State not given"])

df = pd.DataFrame([
    dict(crop="Soybeans", state="IA", location="Polk Co", date_reported=dt.date(2026, 9, 20),
         raw_text="Polk Co, IA: 62 bpa."),
    dict(crop="Corn", state="IL", location="Clark Co", date_reported=None,
         raw_text="Clark Co, IL: 230 bpa."),
    dict(crop="Corn", state="IL", location="Adams Co", date_reported=dt.date(2026, 9, 25),
         raw_text="Adams Co, IL: 241 bpa."),
    dict(crop="Corn", state="IA", location="Story Co", date_reported=dt.date(2026, 9, 28),
         raw_text="Story Co, IA: 255 bpa."),
    dict(crop="Corn", state="IL", location="Brown Co", date_reported=dt.date(2026, 9, 21),
         raw_text="Brown Co, IL: 222 bpa — “so far”…"),
])
check("PDF order: corn first, states by name, dated by date then undated",
      list(RT.ordered(df)["location"]), ["Brown Co", "Adams Co", "Clark Co", "Story Co", "Polk Co"])
check("place order", list(RT.ordered(df, by="place")["location"]),
      ["Adams Co", "Brown Co", "Clark Co", "Story Co", "Polk Co"])
check("sections", [(c, [n for n, _ in s]) for c, s in RT.sections(RT.ordered(df))],
      [("Corn", ["Illinois", "Iowa"]), ("Soybeans", ["Iowa"])])

table = RT.md_table(RT.ordered(df)[lambda d: d["state"] == "IL"], query="bpa")
check("table: header + one line per report", table.count("\n") + 1, 2 + 3)
check("table: dated row", table.splitlines()[2],
      "| :gray[Sep 21] | **Brown Co, IL:** 222 :yellow-background[bpa] — “so far”… |")
check("table: undated row has an empty date cell", table.splitlines()[4].startswith("|  | **Clark"),
      True)
check("table without dates", RT.md_table(df.iloc[[1]], dates=False).splitlines(),
      ["| Report |", "| --- |", "| **Clark Co, IL:** 230 bpa. |"])
check("a pipe in a report can't split the cell",
      RT.md_report("Polk Co, IA: 60|62 bpa", "Polk Co", "IA"), r"**Polk Co, IA:** 60\|62 bpa")

# --- downloads -----------------------------------------------------------------------
pdf = RT.to_pdf(RT.ordered(df), "Yield reports · 2026", "made-up rows")
import fitz  # noqa: E402

doc = fitz.open("pdf", pdf)
text = "".join(p.get_text() for p in doc)
check("pdf: one page", doc.page_count, 1)
for want in ("Yield reports · 2026", "Corn", "Illinois", "Sep 21", "Brown Co, IL:",
             "222 bpa — “so far”…", "Page 1 of 1"):
    check(f"pdf has {want!r}", want in text, True)

from docx import Document  # noqa: E402

word = Document(io.BytesIO(RT.to_docx(RT.ordered(df), "Yield reports · 2026", "made-up rows")))
paras = [p.text for p in word.paragraphs]
check("word: headings", [p.text for p in word.paragraphs if p.style.name.startswith("Heading")],
      ["Corn", "Illinois", "Iowa", "Soybeans", "Iowa"])
check("word: a dated report", "Sep 21   Brown Co, IL: 222 bpa — “so far”…" in paras, True)
check("word: an undated report", "Clark Co, IL: 230 bpa." in paras, True)
bold = [r.text for p in word.paragraphs for r in p.runs if r.bold]
check("word: labels bold", "Adams Co, IL: " in bold, True)

if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("All report-text checks pass.")
