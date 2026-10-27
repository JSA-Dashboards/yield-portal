"""
The summary at the top of the weekly email and of Report text's PDF: for each crop,
Explore's headline tiles and two charts (reported yields by crop year; average by
state, this season against the last), after a yellow box listing the new reports.
weekly_email.py and report_text.to_pdf build from this one copy, so the two read
the same (Kolten, 2026-10-27: "make them basically mirror each other").

Charts are Altair rendered to PNG (vl-convert-python), the JSA logo watermarked under
the marks; the HTML is tables with inline styles, which Outlook (Word) and PyMuPDF's
Story layout both render.
"""
import datetime as dt
import html
import io
import pathlib
import re

import altair as alt
import pandas as pd

import data
import report_text as RT

HERE = pathlib.Path(__file__).resolve().parent
DARK, BLUE, GRAY, LINE = "#32373c", "#0693e3", "#6b7280", "#e5e7eb"
UP, DOWN = "#15803d", "#b91c1c"
NEW_BG = RT.NEW_BG                      # the past week's reports (Report text's yellow too)
FONT = "Segoe UI, Arial, sans-serif"
EMAIL_WIDTH_PX = 684          # the email's content column (720 less its padding)
PDF_WIDTH_PT = 504            # a letter page less report_text.to_pdf's margins
e = html.escape


def counted(df, crop):
    """What Explore counts for a crop by default: clean + approved field reports
    (seed plots left out), corn without silage."""
    f = df[(df["crop"] == crop) & df["status"].isin(["clean", "approved"])
           & df["source"].isin(data.HEADLINE_SOURCES)]
    return f[~f["is_silage"]] if crop == "Corn" else f


# --- charts (Altair -> PNG, embedded) ------------------------------------------------
LOGO = HERE / "assets" / "jsa_logo.png"     # the JSA logo (jpsi.com's logo-full.png)
WATERMARK_OPACITY = 0.07                     # as the dashboards draw it (_wm_center)


def _png(chart) -> bytes:
    import vl_convert as vlc
    # Segoe UI on the PC; the Droplet (Linux) has none of it, so fall back
    chart = (chart.configure(font="Segoe UI, Liberation Sans, DejaVu Sans, Arial, sans-serif",
                             background="transparent")
             .configure_view(stroke=None)
             .configure_title(fontSize=13, anchor="start", color=DARK, fontWeight=600)
             .configure_axis(labelColor=GRAY, titleColor=GRAY, gridColor="#eef2f6",
                             domainColor=LINE, tickColor=LINE))
    return _watermark(vlc.vegalite_to_png(chart.to_json(), scale=2))


def _watermark(png: bytes) -> bytes:
    """The JSA logo, faint and centred under the chart, the way the dashboards mark
    theirs (Crop Conditions' _wm_center: the full logo, 55% of the width, 7% opacity,
    below the marks). The chart renders on a transparent background and is laid over
    white + logo, so bars and text stay crisp."""
    from PIL import Image
    chart = Image.open(io.BytesIO(png)).convert("RGBA")
    canvas = Image.new("RGBA", chart.size, (255, 255, 255, 255))
    if LOGO.exists():
        logo = Image.open(LOGO).convert("RGBA")
        w = int(chart.width * 0.55)
        h = round(logo.height * w / logo.width)
        logo = logo.resize((w, h), Image.LANCZOS)
        logo.putalpha(logo.getchannel("A").point(lambda a: round(a * WATERMARK_OPACITY)))
        canvas.alpha_composite(logo, ((chart.width - w) // 2, (chart.height - h) // 2))
    canvas.alpha_composite(chart)
    out = io.BytesIO()
    canvas.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()


def box_chart(f, crop):
    y = f.dropna(subset=["yield_bpa"])[["crop_year", "yield_bpa"]].copy()
    y["crop_year"] = y["crop_year"].astype(int)
    return alt.Chart(y).mark_boxplot(
        size=26, color=BLUE, outliers={"size": 18, "opacity": 0.5},
    ).encode(
        x=alt.X("crop_year:O", title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("yield_bpa:Q", title="Reported yield (bpa)", scale=alt.Scale(zero=False)),
    ).properties(width=300, height=210, title=f"{crop}: reported yields by crop year")


def state_chart(f, crop, year, prev, years_all):
    y = f[f["crop_year"].isin([year, prev])].dropna(subset=["yield_bpa"])
    g = (y.groupby(["state", "crop_year"])
         .agg(avg=("yield_bpa", "mean"), n=("yield_bpa", "size")).reset_index())
    # most reports this season first; a tie goes to the state with more reports in all
    # years, then by name, as on Explore's By-state bars
    total = f.dropna(subset=["yield_bpa"]).groupby("state").size()
    now = g[g["crop_year"] == year].set_index("state")["n"]
    top = sorted(now.index, key=lambda s: (-now[s], -total.get(s, 0), s))[:10]
    g = g[g["state"].isin(top)].copy()
    g["crop_year"] = g["crop_year"].astype(int)
    # each state's report count this season beside its name: how much its bar rests on
    n_now = g[g["crop_year"] == year].set_index("state")["n"]
    rows = {s: f"{s} ({int(n_now[s])})" for s in top}
    order = [rows[s] for s in top]
    g["row"] = g["state"].map(rows)
    pinned = data.year_scale(years_all)            # each year in its portal colour
    shown = [y for y in (year, prev) if y is not None]
    colours = dict(zip(pinned.domain, pinned.range))
    height = max(150, 24 * len(top))
    band = alt.Scale(paddingInner=0.3)
    bars = alt.Chart(g).mark_bar(cornerRadiusEnd=2).encode(
        y=alt.Y("row:N", sort=order, title=None, scale=band),
        x=alt.X("avg:Q", title="Avg reported yield (bpa)"),
        color=alt.Color("crop_year:N", title=None,
                        scale=alt.Scale(domain=shown, range=[colours[y] for y in shown]),
                        legend=alt.Legend(orient="bottom", direction="horizontal")),
        yOffset=alt.YOffset("crop_year:N", sort="descending",
                            scale=alt.Scale(paddingInner=0.15)),
    )
    if prev is None:
        return bars.properties(width=278, height=height, title=f"{crop}: average by state, {year}")
    # each state's change beside its pair of bars: small, green up / red down, so it
    # reads at a glance without competing with the bars
    chg = state_changes(g, year, prev, top).assign(row=lambda d: d["state"].map(rows))
    col = alt.Chart(chg).mark_text(
        align="right", baseline="middle", fontSize=11,
    ).encode(
        y=alt.Y("row:N", sort=order, axis=None, scale=band),
        x=alt.value(40),
        text="label:N",
        color=alt.Color("tone:N", legend=None,
                        scale=alt.Scale(domain=["up", "down", "flat"], range=[UP, DOWN, GRAY])),
    ).properties(width=40, height=height,
                 title=alt.TitleParams(f"vs {prev}", fontSize=10, fontWeight="normal",
                                       color=GRAY, anchor="end"))
    return (alt.hconcat(bars.properties(width=232, height=height), col, spacing=6, bounds="flush")
            .resolve_scale(y="shared", color="independent")
            .properties(title=f"{crop}: average by state, {year} vs {prev}"))


state_changes = data.state_changes     # shared with Explore's By-state bars


# --- HTML pieces (tables and inline styles: Outlook renders with Word) ----------------
def _signed(v, suffix=""):
    return "—" if v is None or pd.isna(v) else f"{v:+.1f}{suffix}".replace("-", "−")


def _color(v):
    return GRAY if v is None or pd.isna(v) or abs(v) < 0.05 else (UP if v > 0 else DOWN)


def _tile(label, value, sub="", sub_color=GRAY):
    return (f'<td valign="top" style="border:1px solid {LINE};padding:10px 12px;width:20%">'
            f'<div style="font-size:11px;color:{GRAY}">{label}</div>'
            f'<div style="font-size:19px;font-weight:600;color:{DARK};margin-top:3px">{value}</div>'
            f'<div style="font-size:11px;color:{sub_color};margin-top:3px">{sub or "&nbsp;"}</div>'
            "</td>")


def tiles_html(h, n_new, when="this week"):
    prev = h["prev"]
    avg = "—" if pd.isna(h["avg"]) else f"{h['avg']:.1f} bpa"
    avg_sub = "" if pd.isna(h["avg_change"]) else \
        f"{_signed(h['avg_change'], ' bpa')} ({_signed(h['avg_change_pct'], '%')}) vs {prev}"
    ly_sub = "" if not h["vs_ly_n"] else f"{_signed(h['vs_ly_pct'], '%')} · {h['vs_ly_n']} reports"
    aph_sub = "" if not h["vs_aph_n"] else f"{_signed(h['vs_aph_pct'], '%')} · {h['vs_aph_n']} reports"
    dmg, dmg_n = h["damage"] or ("—", None)
    cells = [
        _tile(f"Reports · {h['latest']}", f"{h['reports']:,}",
              f"+{n_new} {when}" if n_new else f"none new {when}", BLUE if n_new else GRAY),
        _tile("Avg reported yield", avg, avg_sub, _color(h["avg_change"])),
        _tile("Vs like field, prior year", _signed(h["vs_ly"], " bpa"), ly_sub, _color(h["vs_ly"])),
        _tile("Vs APH", _signed(h["vs_aph"], " bpa"), aph_sub, _color(h["vs_aph"])),
        _tile("Most-cited damage", e(dmg), f"{dmg_n} reports" if dmg_n else ""),
    ]
    return ('<table role="presentation" width="100%" cellspacing="6" cellpadding="0" '
            f'style="width:100%;border-collapse:separate"><tr>{"".join(cells)}</tr></table>')


def _place(r):
    state = r.state if isinstance(r.state, str) and r.state else ""
    if not (isinstance(r.location, str) and r.location):
        return state or "Place not given"
    return f"{r.location}, {state}" if state else r.location


def new_box(new, lo, hi, heading="New this week", period="in the past week"):
    """The yellow box listing the new reports (`new`, dated lo..hi) by crop."""
    span = f"{lo:%b} {lo.day} – {hi:%b} {hi.day}"
    if new.empty:
        return (f'<p style="margin:0 0 14px 0;font-size:13px;color:{GRAY}">No reports dated '
                f"{span}. Reports dated {period} are highlighted in yellow below.</p>")
    lines = []
    for crop in data.CROPS:
        rows = RT.ordered(new[new["crop"] == crop])
        if rows.empty:
            continue
        items = " · ".join(
            e(_place(r)) + (f" {r.yield_bpa:g}" if pd.notna(r.yield_bpa) else "")
            + (f" ({e(t)})" if (t := RT.source_tag(r.source, r.source_file)) else "")
            for r in rows.itertuples())
        lines.append(f"<b>{crop} ({len(rows)}):</b> {items}")
    other = new[~new["crop"].isin(data.CROPS)]
    if len(other):
        lines.append(f"<b>Crop not given ({len(other)}):</b> "
                     + " · ".join(e(_place(r)) for r in other.itertuples()))
    return (f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            f'style="margin:0 0 16px 0"><tr><td style="background-color:{NEW_BG};'
            f'padding:10px 12px;font-size:13px;color:{DARK};line-height:1.45">'
            f"<b>{heading}: {len(new)} report{'s' if len(new) != 1 else ''} dated {span}</b>, "
            "highlighted in yellow in the report text below.<br>"
            + "<br>".join(lines) + "</td></tr></table>")


INTRO = (f'<p style="margin:0 0 12px 0;font-size:13px;color:{GRAY}">Seed plots customers shared '
         "appear in the report text, tagged with the company, and stay out of the tiles and "
         "charts.</p>")


def footnote(year):
    return ("Averages are of the reports themselves, not county or state estimates, and count "
            f"reviewed reports only (silage left out of corn). State chart: each state's {year} "
            "report count in parentheses and, beside its bars, its average against last year's, "
            f"in gray where either year has fewer than {data.THIN} reports.")


def crop_sections(df, year, new, crops=data.CROPS, when="this week", img_width=330):
    """Per crop with reports this season: its heading, the tiles and the two charts.
    `df` is every live report (all years: the charts and the comparisons need them),
    `new` the reports called new. -> (html with cid: images, {cid: png bytes})."""
    years_all = sorted(int(y) for y in df["crop_year"].dropna().unique())
    prev_years = [y for y in years_all if y < year]
    prev = max(prev_years) if prev_years else None
    images, parts = {}, []
    for crop in crops:
        f = counted(df, crop)
        if f[f["crop_year"] == year].empty:
            continue
        h = data.headline(f, year, prev)
        n_new = int(((new["crop"] == crop) & new["source"].isin(data.HEADLINE_SOURCES)).sum())
        box_cid, state_cid = f"{crop.lower()}_box", f"{crop.lower()}_states"
        images[box_cid] = _png(box_chart(f, crop))
        images[state_cid] = _png(state_chart(f, crop, year, prev, years_all))
        w = img_width
        parts.append(                    # kept on one page in the PDF
            '<div style="page-break-inside:avoid">'
            f'<h2 style="font-family:{FONT};font-size:17px;color:{BLUE};margin:22px 0 4px 0">'
            f"{crop}</h2>" + tiles_html(h, n_new, when)
            + '<table role="presentation" width="100%" cellspacing="6" cellpadding="0"><tr>'
            f'<td valign="top" width="50%"><img src="cid:{box_cid}" width="{w}" '
            f'alt="{crop} reported yields by crop year" style="display:block;width:{w}px"></td>'
            f'<td valign="top" width="50%"><img src="cid:{state_cid}" width="{w}" '
            f'alt="{crop} average by state" style="display:block;width:{w}px"></td>'
            "</tr></table></div>")
    return "".join(parts), images


def pdf_summary(df, year, crops, new, since, today=None):
    """Report text's PDF: the email's summary for the page's crop year and crops, with
    the reports dated since the page's "Highlight new since" day as the new ones (the
    email uses the past week). -> (html, {cid: png})."""
    today = today or dt.date.today()
    dates = [d for d in new["date_reported"] if isinstance(d, dt.date)]
    hi = max(dates + [today]) if dates else today
    when = f"since {since:%b} {since.day}"
    parts, images = crop_sections(df, year, new, crops, when=when)
    body = (INTRO + new_box(new, since, hi, heading=f"New {when}", period=when) + parts
            + f'<p style="margin:8px 0 0 0;font-size:11px;color:{GRAY}">{footnote(year)}</p>')
    # the email's 684 px column on a letter page's 504 pt: every size scaled alike, so
    # the PDF is the email made smaller rather than re-laid out
    k = PDF_WIDTH_PT / EMAIL_WIDTH_PX
    body = re.sub(r"(\d+(?:\.\d+)?)px", lambda m: f"{float(m.group(1)) * k:.1f}pt", body)
    body = re.sub(r'(<img [^>]*?)width="(\d+)"', lambda m: f'{m.group(1)}width="{round(int(m.group(2)) * k)}"',
                  body)
    return body, images

