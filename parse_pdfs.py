"""
Parse the annual Ag Trader Talk yield PDFs into structured observation rows.

Two layouts are handled:
  * "Template" years (2025, 2026): Crop header -> State header -> one line per
    observation, e.g. "Adams Co, IN: 105-110 day corn ... 248 bpa, 18.6% moisture".
  * "Yields" years (2023, 2024): Crop header -> flat list, state carried in the
    location token of each line ("Centerville, IA (SE IA) - ...").

Nothing is thrown away: the full observation text is kept in `raw_text`. The
structured columns (yield, moisture, acres, APH, maturity) are best-effort regex
pulls over that text and may be null when the wording doesn't expose them.

Output: yield_observations.csv + .json in this folder, plus a stats summary.
"""
import fitz  # pymupdf
import datetime as dt
import re
import os
import csv
import json
import hashlib
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
DL = r"C:\Users\KoltenPostin\Downloads"

# crop_year is the harvest year; file -> year
FILES = {
    "2023 Yields.pdf": 2023,
    "2024 Yields.pdf": 2024,
    "2025 Yield Template.pdf": 2025,
    "2026 Yield Template 9.28.26.pdf": 2026,
}

STATE_ABBR = {
    "ALABAMA": "AL", "ARKANSAS": "AR", "GEORGIA": "GA", "ILLINOIS": "IL",
    "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY",
    "LOUISIANA": "LA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
    "MISSOURI": "MO", "NEBRASKA": "NE", "NORTH DAKOTA": "ND", "OHIO": "OH",
    "OKLAHOMA": "OK", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX",
    "WISCONSIN": "WI", "NORTH CAROLINA": "NC", "SOUTH CAROLINA": "SC",
}
ABBRS = set(STATE_ABBR.values())
# full-state-name header line (exact), case-insensitive
STATE_HDR_RE = re.compile(
    r"^(%s)\s*$" % "|".join(sorted(STATE_ABBR, key=len, reverse=True)),
    re.IGNORECASE)

CROP_HDR_RE = re.compile(
    r"^(corn|soybeans?|beans|wheat|milo|sorghum|silage)\s*:?\s*$", re.IGNORECASE)
# Soybean yields stop here (bpa): past it a report can only be corn. The review
# checks' "corn?" flag and an email report that names no crop both use it.
SOY_MAX = 100


def norm_crop(s):
    s = s.lower().rstrip(":").strip()
    if s in ("beans", "soybean", "soybeans"):
        return "Soybeans"
    return s.capitalize()


# Separators that close a location prefix: colon, hyphen, en/em dash, open paren.
_SEP = r"[:\-–—(]"
# "<place/county>, ST:"  or  "<place> ST –"   (ST = 2-letter abbrev)
NEW_OBS_RE = re.compile(
    r"^([A-Z][\w.&'/, ]{0,44}?)[, ]+(%s)\b\s*%s" % ("|".join(ABBRS), _SEP))
# "<Name> Co/County <FullStateName>"  e.g. "Monona County Iowa Silage Appraisals".
# The county word is required so a sentence that merely names a state
# ("Common story for Indiana, ...") is not taken as a new observation.
_FULL = "|".join(sorted(STATE_ABBR, key=len, reverse=True))
FULLNAME_RE = re.compile(
    r"^([A-Z][\w'&/ ]{0,30}?\s(?:Co\.?|County|Parish))[, ]+(%s)\b" % _FULL,
    re.IGNORECASE)
# Mixed-case abbreviation after a county word: "Howard Co Ia - near the Mn line".
_TITLE = {ab.title(): ab for ab in ABBRS}
TITLE_CO_RE = re.compile(
    r"^([A-Z][\w.' ]{0,30}?\s(?:Co\.?|County))[, ]+(%s)\b" % "|".join(_TITLE))
# No separator after the state (common in 2023): "Shelby Co IL 280 acres",
# "Tuscola IL 22 acres", and with a comma: "Macon Co, IL 30 acres", "Elgin, IA 18-20%"
# (without the comma form, those lines were glued onto the report above them). The
# place must be 1-2 capitalised words (+ optional county word) so "Great IL
# crop"-style sentences rarely qualify.
LOOSE_RE = re.compile(
    r"^([A-Z][a-z.']+(?:\s+[A-Z][a-z.']+)?(?:\s+(?:Co\.?|County|Parish))?),?\s+(%s)\b"
    % "|".join(ABBRS))
# A county with no state, then a separator: "Polk Co – 150 bpa" in a run of
# Iowa reports. Starts a new report in the state of the one before it (or of
# the email's "Illinois:" heading). A hyphen may run straight into the report:
# "Greene County-picked one field", "Doniphan Co-150 acres" (not "Co-op").
NOSTATE_CO_RE = re.compile(
    r"^((?:[A-Z][a-z.']+\s+){1,2}(?:Co\.?|County|Parish))"
    r"(?:\s*[–—:\-]\s|-(?!op\b)(?=[a-z\d]))")
# Hyphens for commas, region before the state: "Linn Co-Central MO- 100 acres",
# "Cooper Co-West Central MO-", "Logan Co-Western KS-dryland made 40".
HYPHEN_CO_RE = re.compile(
    r"^((?:[A-Z][a-z.']+\s+){1,2}(?:Co\.?|County|Parish))\s*[–—\-]\s*"
    r"(?:(?:(?:North|South|East|West)(?:east|west|ern)?|Central|NE|NW|SE|SW|NC|SC|EC|WC)"
    r"(?:\s+Central)?\s+)?(%s)\b" % "|".join(ABBRS))
# Regional block led by the state itself: "AR Delta:", "MS yields –", "AR –".
LEAD_STATE_RE = re.compile(
    r"^(%s)\b\s*([A-Za-z ]*?)\s*%s" % ("|".join(ABBRS), _SEP))


def parse_location(line):
    """Return (location, state_abbr). location may be None for a regional block
    led by the state; state is None when the line starts no observation, and ""
    for a county that names no state (the caller carries the previous one)."""
    m = LEAD_STATE_RE.match(line)
    if m:
        return (m.group(2).strip() or None), m.group(1)
    m = NEW_OBS_RE.match(line)
    if m:
        return m.group(1).strip().rstrip(",-– "), m.group(2)
    m = FULLNAME_RE.match(line)
    if m:
        return m.group(1).strip(), STATE_ABBR[m.group(2).upper()]
    m = TITLE_CO_RE.match(line)
    if m:
        return m.group(1).strip(), _TITLE[m.group(2)]
    m = LOOSE_RE.match(line)
    if m:
        return m.group(1).strip(), m.group(2)
    m = HYPHEN_CO_RE.match(line)
    if m:
        return m.group(1).strip(), m.group(2)
    m = NOSTATE_CO_RE.match(line)
    if m:
        return m.group(1).strip(), ""
    return None, None


def is_continuation(line):
    """True when a line clearly belongs to the previous observation rather than
    starting a new one: lowercase start, a leading number (sub-bullet yields),
    or bullet markers."""
    if not line:
        return True
    c = line[0]
    if c.islower() or c.isdigit():
        return True
    if c in "•-–—*":
        return True
    return False


# --- metric extraction over raw text ---------------------------------------
# A yield is a number followed by a yield unit; an optional "lo-" / "lo to"
# prefix captures ranges ("225-230 bpa"). "230 dry" means 230 bu dry.
# (?<![/\d]) keeps a date out of a range: "planted 4/12 – 241 bu/acre" is 241,
# not 12-241. (?<!\d\.) skips the tail of a decimal but still reads "vs.73 bpa".
YIELD_RE = re.compile(
    r"(?:(?<![/\d])(?<!\d\.)(\d{2,3})\s*(?:-|–|to)\s*)?(?<![/\d])(?<!\d\.)(\d{2,3}(?:\.\d+)?)"
    r"\+?\s*-?\s*"
    r"(?:bpa|bu(?:shels?)?(?:\s*/\s*ac(?:re)?|\s+per\s+acre)?|dry)\b",   # "80+ bpa", "214-BPA"
    re.IGNORECASE)
# A road number is not a yield: "along Hwy 30- 66 bpa" is 66.
_ROAD = re.compile(r"(?:hwy|highway|route|rte|interstate|i-)\s*$", re.I)
# Context classifiers. A line usually mixes this year's yield with last year's,
# the expectation, and differences ("down 12 bu", "20 bpa less than APH");
# only the first kind is the observation's yield.
_PRE_DELTA = re.compile(   # "down 12 bu", "better than expected by about 10 bu"
    r"\b(down|up|off|by|plus|minus)(?:\s+(?:about|around|roughly|approximately|"
    r"nearly|almost|just|over))?\s*$", re.I)
_POST_DELTA = re.compile(    # "10bpa behind last year" is a difference, not LY 10
    r"^\s*(?:\w+\s+){0,2}?(better|less|more|worse|over|under|higher|lower|"
    r"above|below|short|off|behind|ahead)\b", re.I)
# [^\w.] — never look across a full stop: in "228 dry. 12 off last year" the
# "last year" belongs to the next sentence, not to 230.
# Intervening words must be plain words — not numbers ("68 bpa vs 73 last year":
# the "last year" is 76's) and not comparisons ("241 bpa, above last year": 238
# is this year's, compared to last year).
_COMPARE = (r"above|below|better|worse|over|under|than|higher|lower|compared|vs|"
            r"versus|from|off|up|down|same|as|like|similar|to|of|behind|ahead")
_LY_POST = re.compile(
    r"^[^\w.]{0,3}(?:(?!(?:%s)\b)[A-Za-z]+\s+){0,2}?(last year|ly\b|a year ago|"
    r"year ago|prior year|in 20\d\d|last yr)" % _COMPARE, re.I)
# "vs 76 last year" / "vs 62 ly" / "vs. 55bpa last year" -> last-year yield
LY_VS_RE = re.compile(
    r"\bvs\.?\s+(\d{2,3}(?:\.\d+)?)\s*(?:bpa|bu\w*(?:/ac\w*)?)?\s*"
    r"(?:last year|ly\b|a year ago|in 20\d\d)", re.I)
_LY_WORDS = re.compile(r"last year|\bly\b|last yr|a year ago|prior year", re.I)
_COMPARE_BEFORE = re.compile(
    r"(?:than|vs\.?|versus|from|over|under|above|below|as|to|like|"
    r"compared(?:\s+(?:to|with))?)\s*$", re.I)


def _ly_before(before):
    """True when the text just before a yield puts that yield in last year:
    "LY 225", "last year was 260", "Last year the county average was 211.69".
    A comparison ("better than last year, made 245") does not count."""
    sent = re.split(r"[.;]\s", before)[-1]          # stay inside the sentence
    hits = list(_LY_WORDS.finditer(sent))
    if not hits:
        return False
    m = hits[-1]
    if _COMPARE_BEFORE.search(sent[:m.start()]):
        return False
    gap = len(sent) - m.end()
    opens_sentence = not sent[:m.start()].strip()
    return gap <= 25 or (opens_sentence and gap <= 55)


# "250 LY", "225 bpa LY" — a bare last-year figure, used when nothing else gave one
LY_TAIL_RE = re.compile(
    r"(?<![\d.])(\d{2,3}(?:\.\d+)?)\s*(?:bpa|bu(?:/ac\w*)?)?\s*(?:\bLY\b|last year|a year ago)",
    re.I)
# "240 bu/ac expected" is the expectation; but in "yield 52 bpa expected 215bpa"
# the expectation is the NEXT number, so 47 stays the actual.
# \b pins the whole word; without it \w* backtracks to "expect" and the
# lookahead then sees "ed 220bpa" and wrongly passes.
# Likewise "went 54.5 bpa, expected 42": a figure after "expected" is the
# expectation. A percentage there ("240 bu/ac expected – 100% irrigated") isn't;
# (?!\d) stops \d{2,3} backtracking to "10" of "100%", and (?!\.\d) — not
# (?![\d.]) — still lets "expected 40." end a sentence.
_EXP_POST = re.compile(
    r"^\W{0,3}(expect\w*|estimat\w*|target\w*|budget\w*)\b"
    r"(?!\W{0,3}\d{2,3}(?:\.\d+)?(?!\d)(?!\.\d)(?!\s*%))", re.I)
# What the farmer thought it would make is an expectation too: "Producer a bit
# surprised, thought it was 215" (the verb is optional here because a bare
# "was 215" match starts at "was"; the context before it ends at "thought it").
_THOUGHT = (r"(?:thought|figured|guessed)(?:\s+(?:it|they|we|he|she))?"
            r"(?:\s+(?:was|were|would be|'d be|would make|would go|would run|might be|could be))?")
# A yield check is an estimate made before harvest: "190 bpa vs a mid-Aug yield check
# at 215 bpa", "field checked in July at 230bpa".
_EXP_PRE = re.compile(r"(expect\w*|hop\w* (?:for|to)|%s|yield\s+checks?(?:\s+(?:at|of|was|were|"
                      r"showed|said))?|checked\s+(?:in|on|during)\s+\w+\s+(?:at|of))\W{0,10}$"
                      % _THOUGHT, re.I)
# ...but "better than expected 140 bpa" is a finished comparison: 140 is the
# yield; so is "better than we thought 140 bpa".
_EXP_DONE = re.compile(r"\b(?:than|as)\s+(?:expected|(?:\w+\s+)?thought)\W{0,3}$", re.I)
# A "last year" right after a figure that opens its own clause belongs to the next
# figure: in "estimate 231, fwiw last year silage estimate was 238", 238 is last
# year's and 231 this year's; likewise "made 68, last year was 73".
_LY_LEADS = re.compile(r"^[^\d.;]{0,30}?\b(?:was|were|at|of|made|did|went)\s+\d{2,3}", re.I)
# "less/more than" marks a difference at any size ("40 to 60 bushel less than
# last year"), unlike "better than", which also follows real yields.
_LESS_MORE = re.compile(r"^\s*(?:\w+\s+)?(less|more|fewer)\s+than\b", re.I)
# So do a year-on-year change and a named difference, at any size: "silage
# running 30-50 bu higher YoY (210-240 bpa)", "seeing 15-25 bu difference".
_DIFF_POST = re.compile(
    r"^\s*(?:(?:higher|lower|more|less|better|worse|up|down)\s+"
    r"(?:yoy|y/y|year[- ]over[- ]year)\b|(?:of\s+)?(?:difference|swing|spread)\b)", re.I)
# The APH is not a yield, before or after its figure: "APH 65 bpa", "APH was 42 bpa",
# "vs 65 bpa APH" — but in "231 bpa aph 210" the figure after "aph" is the APH.
_APH_NUM_AFTER = r"\s*(?:is|was|of|at|=|:)?\s*\d{2,3}(?:\.\d+)?(?!\d)(?!\s*(?:acres?\b|ac\b|a\b))"
_APH_PRE = re.compile(r"aph\b(?:\s*(?:is|was|of|at|=|:)\s*)?(?:about\s+|around\s+)?\W{0,8}$", re.I)
_APH_POST = re.compile(r"^\s*(?:(?:bpa|bu\w*(?:/ac\w*)?)\s*)?aph\b(?!%s)" % _APH_NUM_AFTER, re.I)


# What must NOT follow a bare yield number: moisture/acres/maturity/test-weight
# units, a unit we already parse (handled by YIELD_RE), or the start of a range
# like "20-22%" (which would otherwise read as 20). Case-insensitive even inside
# LEAD_NUMBER_RE, so "160 A 71.4 ave" reads 160 as acres; an "a" before a word
# ("248 A lot of...") is not acres.
_NOT_YIELD_TAIL = (    # (?!\.\d) not (?![\d.]): "corn avg 238." ends a sentence
    r"(?!\d)(?!\.\d)(?!(?i:\s*(?:%|acres?\b|ac\b|a\b(?!\s+[a-z])|-?day|#|lbs?\b|tw\b|test|"
    r"mst|moist|degree|tons?\b|bu|bpa|ft\b|inch)|\s*(?:-|–|to)\s*\d))")
# Unit-less yields after a harvest verb: "went 287", "running 240-280",
# "LY was 259", "in the 190 range". Only consulted when no unit-bearing current
# yield was found.
BARE_YIELD_RE = re.compile(
    r"\b(?:went|made|averag(?:ed|ing)|avg|yielded|came in at|running|ran|did|"
    r"making|was|yields?(?:\s+of)?|in the|"
    r"estimate[ds]?|apprais(?:ed|ing|al)(?:\s+(?:at|of))?)\s+"     # "silage estimate 218"
    r"(?:about\s+|around\s+|right at\s+|roughly\s+)?"
    r"(\d{2,3}(?:\.\d+)?)(?:\s*(?:-|–|to)\s*(\d{2,3}(?:\.\d+)?))?" + _NOT_YIELD_TAIL,
    re.I)
POST_AVG_RE = re.compile(r"(?<![\d.])(\d{2,3}(?:\.\d+)?)\s+(?:avg|ave|average)\b", re.I)
# A bare expectation: "went 54.5 bpa, expected 42."
EXP_NUM_RE = re.compile(
    r"\b(?:expect(?:ed|ing|ation)?|(?:thought|figured)\s+(?:it|they)\s+"
    r"(?:was|were|would be|'d be|would make))\s+(?:about\s+|around\s+)?(\d{2,3}(?:\.\d+)?)"
    r"(?!\d)(?!\.\d)(?!\s*%)", re.I)
# Location then a bare figure, nothing between: "Mitchell Co IA 248" (emailed lists).
LEAD_NUMBER_RE = re.compile(
    r"^[^:\d]{2,60}?\b(?:%s)\b[\s:,\-]+(\d{2,3}(?:\.\d+)?)%s" % ("|".join(ABBRS), _NOT_YIELD_TAIL))
# Unit BEFORE the number — the seed-plot format in 2025: "Avg bpa 259.8".
PRE_UNIT_RE = re.compile(
    r"\b(?:bpa|bu/ac(?:re)?)\s*[:=]?\s*(\d{2,3}(?:\.\d+)?)" + _NOT_YIELD_TAIL, re.I)


def _classify(text, start, end, val, cur, ly, exp, lo=None):
    before = text[max(0, start - 30): start]
    after = text[end: end + 30]
    long_before = text[max(0, start - 80): start]
    # a small number with a comparison word after it is a difference,
    # not a yield ("10 bpa better than LY"); "245 bpa, better than APH" is a yield
    if (_PRE_DELTA.search(before) or (val < 45 and _POST_DELTA.match(after))
            or _LESS_MORE.match(after) or _DIFF_POST.match(after)):
        return
    if _APH_PRE.search(before) or _APH_POST.match(after):
        return
    ly_after = _LY_POST.match(after)
    if ly_after and _LY_LEADS.match(text[end + ly_after.end(): end + ly_after.end() + 45]):
        ly_after = None
    if ly_after or _ly_before(long_before):
        ly.append(val)
        return
    if _EXP_POST.match(after) or (_EXP_PRE.search(before) and not _EXP_DONE.search(before)):
        exp.append(val)
        return
    if not (10 <= val <= 400):
        return
    if lo is not None and 10 <= lo <= 400:
        cur.append(lo)
    cur.append(val)


def extract_yields(text):
    """-> (current, last_year, expected) lists of bu/ac values."""
    cur, ly, exp = [], [], []
    for m in YIELD_RE.finditer(text):
        if _ROAD.search(text[max(0, m.start(2) - 12): m.start(2)]):
            continue
        lo = float(m.group(1)) if m.group(1) else None
        if lo is not None and _ROAD.search(text[max(0, m.start(1) - 12): m.start(1)]):
            lo = None
        _classify(text, m.start(), m.end(), float(m.group(2)), cur, ly, exp, lo)
    for m in PRE_UNIT_RE.finditer(text):
        _classify(text, m.start(), m.end(), float(m.group(1)), cur, ly, exp)
    if not ly:
        m = LY_VS_RE.search(text)
        if m:
            ly.append(float(m.group(1)))
    if not ly:
        for m in LY_TAIL_RE.finditer(text):
            if not _PRE_DELTA.search(text[max(0, m.start() - 12): m.start()]):
                ly.append(float(m.group(1)))
                break
    if not cur:
        for m in BARE_YIELD_RE.finditer(text):
            # a bare range "240-280" is lo=group1, hi=group2
            v1 = float(m.group(1))
            v2 = float(m.group(2)) if m.group(2) else None
            if v2 is not None:
                _classify(text, m.start(), m.end(), v2, cur, ly, exp, lo=v1)
            else:
                _classify(text, m.start(), m.end(), v1, cur, ly, exp)
    if not cur:
        # a bare figure right after the location: "Mitchell Co IA 248"
        m = LEAD_NUMBER_RE.match(text)
        if m:
            _classify(text, m.start(1), m.end(1), float(m.group(1)), cur, ly, exp)
    if not cur:
        # the average named after the figure: "1/2 done on corn w 236 avg so far"
        for m in POST_AVG_RE.finditer(text):
            _classify(text, m.start(1), m.end(1), float(m.group(1)), cur, ly, exp)
    if not exp:
        m = EXP_NUM_RE.search(text)
        if m:
            exp.append(float(m.group(1)))
    # Several fields and a stated farm average: the average speaks for the report
    # ("120 acres made 190 bpa ... Overall farm avg near 238 bpa" is 238).
    m = FARM_AVG_RE.search(text)
    if m and float(m.group(1)) in cur and not _other_place_between(text, m.start()):
        v = float(m.group(1))
        cur = [v] + [c for c in cur if c != v]
    return cur, ly, exp


_PLACE_MARK = re.compile(r"\b(?:%s)\b|\b(?:Co\.?|County|Parish)\b" % "|".join(sorted(ABBRS)))


def _other_place_between(text, upto):
    """True when another place is named between the report's first yield and
    `upto`: in "Sac County Iowa ... 220 bpa ... W TN whole farm average of 201",
    the 201 is West Tennessee's, a second report run into the line."""
    first = YIELD_RE.search(text)
    return bool(first and first.end() < upto and _PLACE_MARK.search(text, first.end(), upto))


# "whole farm average 88", "Farm avg was 206", "Overall farm avg near 238": the
# words must sit together, so "whole farm non-irrigated average of 44 ... LY" (a
# last-year figure) isn't taken; and the figure must already read as this year's.
FARM_AVG_RE = re.compile(
    r"\b(?:whole[- ]?farm|overall(?:\s+farm)?|farm)\s+(?:yield\s+)?(?:avg|average)\b"
    r"(?:\s+(?:was|is|of|at|near|right at|around|about|so far))*\s*[:=]?\s*"
    r"(\d{2,3}(?:\.\d+)?)(?!\d)(?!\.\d)", re.I)


# --- a report that gives yields for both crops -----------------------------------
_ABBREV = {"co", "st", "ste", "vs", "approx", "mt", "ft", "no", "bu", "ac", "mr", "dr"}
_CORN_WORD = re.compile(r"\bcorn\b|\bsilage\b", re.I)
_SOY_WORD = re.compile(r"\bsoy\w*|\bbeans?\b", re.I)


def _sentences(text):
    """Split at sentence ends, but not after "Co." / "St." / "vs." and the like."""
    out, start = [], 0
    for m in re.finditer(r"[.!?;]\s+(?=[A-Z0-9\"“(])", text):
        word = re.findall(r"([A-Za-z.]+)$", text[start:m.start()])
        if word and word[0].lower().rstrip(".") in _ABBREV:
            continue
        out.append(text[start:m.end()].strip())
        start = m.end()
    out.append(text[start:].strip())
    return [s for s in out if s]


def split_by_crop(text, default_crop=None):
    """{crop: its sentences} when a report gives yields for both crops, else None.
    Each sentence belongs to the crop it names (the first named, if both), or the
    one before it; leading sentences that name none go with the first crop. Only
    a suggestion: "Corn last year. 75 bpa two years ago" in a bean report reads as
    corn here, so a person confirms every split."""
    segs, cur = [], None
    for s in _sentences(text or ""):
        c, b = _CORN_WORD.search(s), _SOY_WORD.search(s)
        crop = ("Corn" if c.start() < b.start() else "Soybeans") if c and b else \
            "Corn" if c else "Soybeans" if b else None
        crop = crop or cur
        if segs and (crop == segs[-1][0] or crop is None):
            segs[-1][1].append(s)
        else:
            segs.append([crop, [s]])
        cur = crop or cur
    if segs and segs[0][0] is None:
        if len(segs) > 1:
            segs[1][1] = segs[0][1] + segs[1][1]
            segs = segs[1:]
        else:
            segs[0][0] = default_crop
    parts = {}
    for crop, ss in segs:
        parts.setdefault(crop, []).extend(ss)
    parts = {c: " ".join(ss) for c, ss in parts.items() if c in ("Corn", "Soybeans")}
    with_yield = {c: t for c, t in parts.items() if extract_yields(t)[0]}
    return with_yield if len(with_yield) > 1 else None


# APH: "APH 210", "APH of 250", "Aph on field is 235ish", "APH was 47 bpa",
# "(205 APH)", "vs 180 aph". [^\d.] stops at a full stop, so in "better than
# APH. Different producer making 240 bpa vs APH 220" the first APH (no value)
# can't grab 240 — the second one gives 220.
# A figure before "APH" is the APH ("vs 60 bpa APH", "vs. 58.5bpa APH") unless "APH"
# names its own: in "90 acres 228 bpa aph 205" 228 is the yield. "(205 APH) 2. 150
# acres" is still 205: a list number or an acreage after it isn't an APH.
APH_RE = re.compile(
    r"\baph\b[^\d.]{0,14}?(\d{2,3}(?:\.\d+)?)"
    r"|(?<![\d.])(\d{2,3}(?:\.\d+)?)\s*(?:(?:bpa|bu(?:/ac\w*)?)\s*)?aph\b(?!%s)" % _APH_NUM_AFTER,
    re.IGNORECASE)

# Maturity, read the way each crop reports it, kept as text so ranges survive:
#   corn  -> relative maturity days: "110 day", "108-112 day", "95-day", "106 Mat"
#   beans -> maturity group: "2.6 maturity", ".6 maturity", "1.2, 1.3's", "1.0 beans"
CORN_RM_RE = re.compile(
    r"\b(\d{2,3})(?:\s*[-–]\s*(\d{2,3}))?\s*[- ]?(?:days?|mat)\b", re.IGNORECASE)
SOY_MG_RE = re.compile(
    r"(?<![\d.])(\d?\.\d)(?:\s*[-–,]\s*(\d?\.\d))?\s*"
    r"(?:maturity|mg\b|['’]s\b|(?:soy)?beans\b)", re.IGNORECASE)


def extract_maturity(text, crop):
    if crop == "Soybeans":
        for m in SOY_MG_RE.finditer(text):
            lo, hi = m.group(1), m.group(2)
            lo = "0" + lo if lo.startswith(".") else lo
            hi = ("0" + hi if hi.startswith(".") else hi) if hi else None
            if 0 <= float(lo) <= 6.5:
                return f"{lo}-{hi}" if hi else lo
        return None
    for m in CORN_RM_RE.finditer(text):       # "within 10 days" etc. fail the range check
        lo, hi = m.group(1), m.group(2)
        if 70 <= int(lo) <= 125 and (not hi or 70 <= int(hi) <= 125):
            return f"{lo}-{hi}" if hi else lo
    return None


# Irrigation as stated in the report. "irrigated yields" is a figure of speech
# ("irrigated yields on dry ground"), not a statement about the field;
# \b after \w* stops backtracking from slipping past that lookahead.
# "non- irrigated": a PDF line wrapped after the hyphen
_NONIRR_RE = re.compile(r"dry\s?land|non[- ]{0,2}irrigat\w*|not irrigat\w*|rain[- ]?fed", re.I)
_IRR_RE = re.compile(
    r"(?<!non-)(?<!non )(?<!non- )(?<!not )\birrigat\w*\b(?!\s+yields)"
    r"|under (?:a |the )?pivot|\bpivots?\b", re.I)


def extract_irrigation(text):
    """'Irrigated' | 'Non-irrigated' | 'Mixed' (both named) | None (not stated)."""
    irr, non = bool(_IRR_RE.search(text)), bool(_NONIRR_RE.search(text))
    if irr and non:
        return "Mixed"
    return "Irrigated" if irr else "Non-irrigated" if non else None


# --- a report that gives yields for several fields, or for both practices ----------
PRACTICES = ("Non-irrigated", "Irrigated")
# The words only: a pivot can mark the dryland corners too ("outside the pivot").
_PRACTICE_RE = re.compile(
    r"(?P<non>dry\s?land|non[- ]{0,2}irrigat\w*|not irrigat\w*|rain[- ]?fed)"
    r"|(?P<irr>(?<!non-)(?<!non )(?<!non- )(?<!not )\birrigat\w*\b(?!\s+yields))", re.I)
# An acreage opens a field's entry: "40 acres", "a 40 acre field", "40 ac at 140",
# "270a went", "160 A 71.4" (but not "248 A lot of...").
_ACRES_RE = re.compile(
    r"(?<![\d.,])\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:\s*(?:acres?|ac)\b|a\b|\s+a\b(?!\s+[a-z]))", re.I)


def _practice_of(m):
    return PRACTICES[0] if m.group("non") else PRACTICES[1]


# Another place inside a report's entries: a county word, a state abbreviation, or a
# state named in full ("... over in SE Iowa 100 acres = 190 BPA").
_ENTRY_PLACE = re.compile(_PLACE_MARK.pattern + r"|(?i:\b(?:%s)\b)" % _FULL)
_STATE_FULL = re.compile(r"\b(?:%s)\b" % _FULL, re.I)


def _ly_field(text, at):
    """True for an acreage that is last year's field: "last year" opens its clause
    ("Last year our 100 acre field next door made 80bpa"). One after a comparison
    ending in "last year" opens the next entry ("220 bpa vs 190 bpa last year 150
    acres made 230 bpa")."""
    near = list(_LY_WORDS.finditer(text[max(0, at - 25): at]))
    if not near:
        return False
    start = max(0, at - 25) + near[-1].start()
    clause = re.split(r"[.;:,!?]\s", text[:start])[-1]
    return not re.search(r"\d", clause)


def split_entries(text):
    """(lead, [(practice, entry text), ...]) when a report gives yields for two or
    more fields or for both practices, else None (Kolten, 2026-10-05: "multiple
    entries, we need to add each one").

    The report is cut wherever an acreage or a practice is named. A piece with no
    figure of its own goes with a neighbour: one naming the practice of the
    entry before it is that entry's remark ("... and the non-irrigated average
    last year"); any other (an acreage, or the other practice, before its
    figure) opens the next entry; the last always closes the last entry. An
    entry's practice is the one it names, else the one named before it. No split
    when a figure comes before the first cut (whose is it?), when a whole-farm or
    overall average is stated (it speaks for the report), when another place is
    named inside the entries, or a state in full after the report's own place
    before them (several reports run into one line: "Town, MN ... NE Iowa beans
    100 acres 50 bushel"), or when fewer than two entries carry a yield."""
    text = text or ""
    # last year's field is a remark, not an entry (_ly_field)
    cuts = {m.start(): None for m in _ACRES_RE.finditer(text) if not _ly_field(text, m.start())}
    cuts.update({m.start(): _practice_of(m) for m in _PRACTICE_RE.finditer(text)})
    if not cuts or FARM_AVG_RE.search(text):
        return None
    starts = sorted(cuts)
    lead = text[:starts[0]]
    own = _ENTRY_PLACE.search(lead)
    if own:      # "NC Nebraska", "SE Iowa": a region of one state, one place
        region = re.match(r"\s+(?:%s)\b" % _FULL, lead[own.end():], re.I)
        own_end = own.end() + (region.end() if region else 0)
    if extract_yields(lead)[0] or (own and _STATE_FULL.search(lead, own_end)):
        return None
    entries, practices, pending = [], [], ""
    for i, (a, b) in enumerate(zip(starts, starts[1:] + [len(text)])):
        piece = text[a:b]
        if extract_yields(pending + piece)[0]:
            entries.append(pending + piece)
            named = _PRACTICE_RE.search(entries[-1])
            practices.append(_practice_of(named) if named else (practices[-1] if practices else None))
            pending = ""
        elif entries and not pending and (b == len(text) or
                                          (cuts[a] is not None and cuts[a] == practices[-1])):
            entries[-1] += piece        # a remark on the entry before
        else:
            pending += piece            # an acreage or practice before its figure
    if pending and entries:
        entries[-1] += pending
    if len(entries) < 2 or _ENTRY_PLACE.search("".join(entries)):
        return None
    return lead, [(p, e.strip()) for p, e in zip(practices, entries)]


# Disease and damage named in the report, one normalised tag each. Weather damage
# (hail, wind, drought...) sits alongside disease because the reports use both
# the same way: to say what took yield.
DISEASE_TAGS = [
    ("Tar spot", r"tar\s?spot"),
    ("Crown/root rot", r"crown\s+(?:root\s+)?rot|root\s+rot"),
    ("Stalk rot/quality", r"stalk\s+(?:rot|quality|issues?)"),
    ("Ear rot/mold", r"ear\s+(?:rot|mold)"),
    ("Rust", r"\brust\b"),
    ("Gray leaf spot", r"gr[ae]y\s+leaf\s+spot|\bgls\b"),
    ("Leaf blight", r"leaf\s+blight|\bnclb\b"),
    ("White spot", r"white\s+spot"),
    ("SDS", r"sudden\s+death|\bsds\b"),
    ("White mold", r"white\s+mold"),
    ("Frogeye", r"frog\s?eye"),
    # On the list so they're caught when they turn up; none had by 2026.
    ("Goss's wilt", r"\bgoss"),
    ("Anthracnose", r"anthracnose"),
    ("Phytophthora", r"phytophthora"),
    ("Disease (general)", r"\bdisease"),
    ("Insects", r"aphids?|insects?|rootworm|corn\s+borer|japanese\s+beetle|stink\s+bugs?"),
    ("Pollination", r"pollinat"),
    ("Compaction", r"compact(?:ion|ed)\b"),
    ("Nutrient deficiency", r"(?:nitrogen|potassium|potash|sulfur|phosphorus|nutrient)\s+"
                            r"(?:deficien\w*|short\w*)|\bdeficien(?:cy|t)\b"),
    ("Hail", r"\bhail"),
    ("Wind/lodging", r"wind[- ]?damage\w*|green\s?snap|lodg\w*|downed|"
                     r"(?:fall(?:ing)?|fell)\s+over|blown\s+(?:down|over)"),
    ("Drought/dry", r"drought|dryness|no rain|lack of rain|never (?:could )?get a rain|"
                    r"(?:very|terribly|extremely|too|pretty|really|severe(?:ly)?|so)\s+dry\b|"
                    r"dry\s+(?:july|august|june|september|season|finish|spell|weather|"
                    r"conditions|stretch|year)|missed (?:the )?rains?|zero rain|little rain|"
                    r"didn.t get (?:much )?rain"),
    ("Excess water", r"too wet|flood|drown|water\s?holes?|standing water|"
                     r"excess(?:ive)? (?:rain|moisture)|nitrogen loss"),
    ("Heat stress", r"heat stress|\b1[01]\d\s?f\b|extreme heat|above normal heat|"
                    r"(?:rain|dryness|dry) and heat|heat and (?:dry|drought)|"
                    r"heat in (?:july|august)"),
    ("Frost", r"\bfrost|\bfreez"),
]
_DISEASE_RES = [(tag, re.compile(rx, re.I)) for tag, rx in DISEASE_TAGS]
# every named disease (the tags before the catch-all)
_SPECIFIC_DISEASES = {t for t, _ in DISEASE_TAGS[:[t for t, _ in DISEASE_TAGS].index("Disease (general)")]}


def extract_disease(text):
    """Comma-joined tags in DISEASE_TAGS order, or None. 'Disease (general)' is
    only kept when no specific disease was named."""
    tags = [tag for tag, rx in _DISEASE_RES if rx.search(text)]
    if "Disease (general)" in tags and _SPECIFIC_DISEASES.intersection(tags):
        tags.remove("Disease (general)")
    return ", ".join(tags) or None


def _first(rx, text, groups=1):
    m = rx.search(text)
    if not m:
        return None
    for g in range(1, groups + 1):
        if m.group(g):
            try:
                return float(m.group(g).replace(",", ""))
            except ValueError:
                return m.group(g)
    return None


def extract_metrics(text, crop):
    cur, ly, exp = extract_yields(text)
    aph = _first(APH_RE, text, 2)
    if isinstance(aph, float) and not (20 <= aph <= 350):
        aph = None
    return {
        "yield_bpa": cur[0] if cur else None,
        "yield_min": min(cur) if cur else None,
        "yield_max": max(cur) if cur else None,
        "n_yields": len(cur),
        "ly_yield": ly[0] if ly else None,
        "expected_yield": exp[0] if exp else None,
        "aph": aph,
        "maturity": extract_maturity(text, crop),
        "irrigation": extract_irrigation(text),
        "disease": extract_disease(text),
        "is_silage": "silage" in text.lower(),
        "is_record": bool(re.search(r"\brecord\b|best ever|best .* ever|all[- ]time",
                                    text, re.IGNORECASE)),
    }


def dedup_hash(crop_year, crop, state, location, raw, part=None):
    """Stable row identity: same year/crop/state/location/text -> same hash,
    regardless of case or how the PDF wrapped the line. The entries of a report
    split by field or practice share its text, so each adds its part
    ("Irrigated", "Non-irrigated|2", "entry|1": by_entry)."""
    text = re.sub(r"\s+", " ", raw.lower()).strip()
    key = f"{crop_year}|{crop}|{state}|{location}|{text}" + (f"|{part}" if part else "")
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def row_hash(row):
    """dedup_hash for a row as it stands, with its part when it's one entry of a
    split report (`_part`, set by by_entry; not a stored column)."""
    return dedup_hash(row["crop_year"], row["crop"], row["state"], row["location"],
                      row.get("raw_text") or "", row.get("_part"))


def entry_metrics(text, crop, lead, practice, entry):
    """One entry's figures: its yields, last year, expectation, APH, maturity and
    record from its own words (APH and maturity from the lead when it gives
    none); silage from the lead and its words ("Dryland, 108 day chopped for
    silage" is that field, not the grain field before it); disease from the
    whole report (often the area's); irrigation its practice, or what its
    words say ("20 acres (under pivot) 210")."""
    m = extract_metrics(entry, crop)
    if m["aph"] is None and lead.strip():
        m["aph"] = extract_metrics(lead, crop)["aph"]
    m["maturity"] = m["maturity"] or (extract_maturity(lead, crop) if lead.strip() else None)
    m.update(irrigation=practice or m["irrigation"], disease=extract_disease(text),
             is_silage="silage" in (lead + " " + entry).lower())
    return m


def entries_of(text, crop):
    """[(part, metrics), ...] for a report split by field or practice, else None.
    A practice with one entry is its part ("Irrigated"), as the practice split
    of 2026-10-05 hashed it; several entries are numbered ("Non-irrigated|2", or
    "entry|1" when no practice is named). A report with yields for both crops is
    left whole: the two-crop split comes first (a person confirms it on Review &
    edit), then each crop's half can split ("dryland corn ... 160. First dryland
    beans 60" would otherwise make 160 a soybean yield)."""
    found = split_entries(text)
    if not found or split_by_crop(text, crop):
        return None
    lead, entries = found
    count = Counter(p for p, _ in entries)
    seen, out = Counter(), []
    for practice, entry in entries:
        seen[practice] += 1
        part = practice if practice and count[practice] == 1 else f"{practice or 'entry'}|{seen[practice]}"
        out.append((part, entry_metrics(text, crop, lead, practice, entry)))
    return out


def by_entry(row):
    """A report -> [it], or one report per entry when its text gives yields for
    several fields or both practices (split_entries): the same words in each
    (raw_text), each with its own entry's figures, its practice and a hash of
    its own."""
    entries = entries_of(row.get("raw_text"), row.get("crop"))
    if not entries:
        return [row]
    out = []
    for part, metrics in entries:
        r = dict(row, **metrics, _part=part)
        r["dedup_hash"] = row_hash(r)
        out.append(r)
    return out


_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\ufeff]")   # email preview padding


def _clean_lines(raw_lines):
    out = []
    for ln in raw_lines:
        ln = _ZERO_WIDTH.sub("", ln.replace("\u202f", " ").replace("\xa0", " "))
        ln = re.sub(r"[ \t]{2,}", " ", ln).strip()
        if ln:
            out.append(ln)
    return out


def parse_pdf(src, crop_year, source_file=None):
    """src: a file path, or the PDF's bytes (e.g. a Streamlit upload)."""
    if isinstance(src, (bytes, bytearray)):
        doc = fitz.open(stream=bytes(src), filetype="pdf")
        name = source_file or "upload.pdf"
    else:
        doc = fitz.open(src)
        name = source_file or os.path.basename(src)
    try:
        raw = [ln for pg in doc for ln in pg.get_text().splitlines()]
    finally:
        doc.close()
    return parse_lines(_clean_lines(raw), crop_year, source_file=name)


def parse_lines(lines, crop_year, default_crop=None, source_file=None,
                report_source="pdf", split_reports=True):
    """Run the observation state machine over cleaned text lines: crop and
    state headers set context, a line opening with a location starts a row,
    anything else continues the current row. A report with yields for several
    fields or both practices becomes one row per entry (by_entry); parse_email
    splits after it settles each crop, so it asks not to."""
    obs = []
    crop = default_crop
    state_hdr = None
    cur = None

    def flush():
        nonlocal cur
        if cur:
            obs.append(cur)
            cur = None

    for ln in lines:
        if CROP_HDR_RE.match(ln):
            flush()
            crop = norm_crop(ln)
            state_hdr = None
            continue
        if STATE_HDR_RE.match(ln):
            flush()
            state_hdr = STATE_ABBR[ln.upper()]
            continue
        loc, st = parse_location(ln)
        if st == "":                    # a county with no state: the last report's state
            st = (cur or {}).get("state") or state_hdr
        starts_new = st is not None and not is_continuation(ln)
        if starts_new:
            flush()
            cur = {
                "crop_year": crop_year,
                "crop": crop or "Unknown",
                "state": st or state_hdr,
                "location": loc,
                "raw_text": ln,
            }
        else:
            if cur is None:
                # orphan line (e.g. aggregate block w/o a clear location) — keep it
                cur = {
                    "crop_year": crop_year,
                    "crop": crop or "Unknown",
                    "state": state_hdr,
                    "location": None,
                    "raw_text": ln,
                }
            else:
                cur["raw_text"] += " " + ln
    flush()

    # attach metrics + hash
    for o in obs:
        o.update(extract_metrics(o["raw_text"], o["crop"]))
        o["dedup_hash"] = dedup_hash(o["crop_year"], o["crop"], o["state"],
                                     o["location"], o["raw_text"])
        o["report_source"] = report_source
        o["source_file"] = source_file
    return [x for o in obs for x in by_entry(o)] if split_reports else obs


# --- single emails ------------------------------------------------------------
# "YIELD:", "Yield:", "YIELD " and resends: "RESEND: Correcting Subject YIELD: ...",
# "CORRECTION: YIELD: ..."
_SUBJ_PREFIX = re.compile(
    r"^\s*(?:(?:re|fw|fwd|resend|correction)\s*:\s*)*(?:correcting subject\s*)?yield\b\s*:?\s*",
    re.I)
_REPORT_SUBJECT = re.compile(r"\byield\b", re.I)


def is_report_subject(subject):
    """Every report email says YIELD in its subject; a conversation with the
    source ("RE: Yields Sharing") doesn't, and holds signatures, not reports."""
    return bool(_REPORT_SUBJECT.search(subject or ""))
# Lines an email drags along: Inky banner (also as raw links), signature, phone.
_EMAIL_NOISE = re.compile(
    r"^(?:\[?external\]?\b|safe\b|report this email|caution\b|more\.\.\.|"
    r"this (?:message|email) (?:came|is|was)|garrett\s+toay\b|ag\s*trader\s*talk|"
    r"sent from my|www\.|<?https?://|\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})", re.I)
_BANNER = re.compile(r"^\s*external\s*\(|inkyphishfence", re.I)
# List markers: "*<tab>Sac Co, IA: ...", "• ...", "-Linn Co-...", "1. ..." — but
# not "1.0 beans".
_BULLET = re.compile(r"^\s*(?:[*•·▪◦‣]\s*|-\s+|-(?=[A-Z])|\d{1,2}[.)]\s+)")
# Words that make up a place name; the lowercase lookahead keeps an all-caps
# state ("SW MO Vernon Co") from being read as part of the county.
_NAME = r"[A-Z](?=[A-Za-z.'\-]*[a-z])[A-Za-z.'\-]+"
_REGION = (r"(?:North|South|East|West|Central|Northeast|Northwest|Southeast|Southwest|"
           r"Northern|Southern|Eastern|Western|Northeastern|Northwestern|Southeastern|"
           r"Southwestern|NE|NW|SE|SW|NC|SC|EC|WC)")
_COUNTY_IN_TEXT = re.compile(
    r"((?:%s\s+)?%s(?:\s+%s)?\s+(?:Co\.?|County))\b" % (_REGION, _NAME, _NAME))
_PLACE_ST = re.compile(r"^(%s(?:\s+%s)?),?\s+(%s)\b" % (_NAME, _NAME, "|".join(ABBRS)))
# Lines that open a new report in a body without state abbreviations.
_LEAD_COUNTY = re.compile(
    r"^((?:%s\s+)?%s(?:\s+%s)?\s+(?:Co\.?|County|county))\b" % (_REGION, _NAME, _NAME))
_LEAD_REGION = re.compile(r"^(%s\s+(?:%s))\b" % (_REGION, _FULL), re.I)
# A line that is only a place: "Lee Co, GA", "Sac Co IA"
_LOCATION_ONLY = re.compile(
    r"^(?:%s\s+)?%s(?:\s+%s)?\s+(?:Co\.?|County|county)\.?,?\s*(?:%s)?[\s.:]*$"
    % (_REGION, _NAME, _NAME, "|".join(ABBRS)))
_LEAD_CROP = re.compile(r"^(corn|soybeans?|beans)\s*[-–:]\s*", re.I)
_CROP_WORDS = {"corn", "soybeans", "soybean", "beans", "silage"}
# A second report run into the same line:
#   "... beans 82-84 bpa. Pleased. Brown Co IL (western) corn avg 238"
_MIDLINE_REPORT = re.compile(
    r"(?<=[.!?;])\s+(?=[A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+)?\s+"
    r"(?:Co\.?|County|Parish)\b,?\s+(?:%s)\b)" % "|".join(ABBRS))


def _email_lines(body):
    """An email body's report lines, without banner, signature or repeats.

    Inky puts the report in the plain-text body twice — a preview line before its
    banner and the real body after it — so when the banner is there only what
    follows it is kept (the preview can be cut short). Noise lines then go,
    reports run together on one line are split, and a line that repeats (or is
    a cut-short start of) a later line is dropped."""
    raw = _clean_lines((body or "").replace("\r", "").splitlines())
    banner = [i for i, ln in enumerate(raw) if _BANNER.search(ln)]
    if banner:
        after = [ln for ln in raw[banner[-1] + 1:]
                 if not _EMAIL_NOISE.match(ln) and not _BANNER.search(ln)]
        if after:
            raw = after
    lines = []
    for ln in raw:
        ln = _BULLET.sub("", ln)
        if (not ln or _EMAIL_NOISE.match(ln) or _BANNER.search(ln)
                or "@agtradertalk.com" in ln.lower()):
            continue
        if STATE_HDR_RE.match(ln.rstrip(": ")):
            lines.append(ln.rstrip(": "))   # "Missouri:" over reports that name no state
            continue
        if ln.endswith(":") and not re.search(r"\d", ln) and not CROP_HDR_RE.match(ln):
            continue                    # a heading such as "Silage numbers NC IA:"
        lines.extend(p.strip() for p in _MIDLINE_REPORT.split(ln) if p.strip())
    # "Lee Co, GA" alone on a line, the report on the next ("Corn: Whole
    # farm made 206 ..."): one report, so join them
    joined = []
    for ln in lines:
        if joined and _LOCATION_ONLY.match(joined[-1]):
            joined[-1] += " " + ln
        else:
            joined.append(ln)
    lines = joined
    keys = [re.sub(r"[\s.…]+$", "", ln.lower()) for ln in lines]
    return [ln for i, ln in enumerate(lines)
            if len(keys[i]) < 20
            or not any(keys[j].startswith(keys[i]) for j in range(i + 1, len(lines)))]


def email_crop(subject, body=""):
    """Crop named in an email's subject, falling back to its body."""
    for s in (subject or "", body or ""):
        low = s.lower()
        soy = re.search(r"\bsoy|\bbeans?\b", low) is not None
        corn = re.search(r"\bcorn\b|silage", low) is not None
        if soy != corn:
            return "Soybeans" if soy else "Corn"
    return None


# Abbreviations that double as directions: NE northeast, NC/SC north/south central.
_DIRECTIONAL = {"NE", "NC", "SC"}


def _first_state(text):
    """First state named in `text`. A directional NE/NC/SC followed by a state
    ("NE MO", "NC IA", "NE Nebraska") is the direction; the state is what follows."""
    text = text or ""
    for m in re.finditer(r"\b(%s)\b" % "|".join(ABBRS), text):
        if m.group(1) in _DIRECTIONAL:
            rest = text[m.end():].lstrip()
            nxt = re.match(r"(%s)\b" % "|".join(ABBRS), rest)
            if nxt:
                return nxt.group(1)
            full = re.match(r"(%s)\b" % _FULL, rest, re.I)
            if full:
                return STATE_ABBR[full.group(1).upper()]
        return m.group(1)
    return None


def _subject_location(subject):
    """(location, state) from a subject: a county in parentheses first
    ("IA Corn (Sac Co IA)"), then a county anywhere ("SW MO Vernon Co corn"),
    then "Place, ST" ("Chatham, IL soybeans"); else just the state."""
    subj = _SUBJ_PREFIX.sub("", subject or "").strip()
    paren = re.search(r"\(([^)]*)\)", subj)
    for text in ([paren.group(1)] if paren else []) + [subj]:
        c = _COUNTY_IN_TEXT.search(text)
        if c:
            return c.group(1), _first_state(text) or _first_state(subj)
    p = _PLACE_ST.match(subj)
    if p and len(p.group(1)) >= 3 and p.group(1).lower() not in _CROP_WORDS:
        return p.group(1), p.group(2)
    return None, _first_state(subj)


def _lead_rows(lines, subject_loc):
    """Split a body that names no states into reports, at lines opening with a
    county ("Lauderdale county, ...", "Northern Vermilion County ..."), a region
    ("Southwest Ohio ...") or a crop ("Corn - ...", "Beans - ..."). Lines
    before any such opener form one report located from the subject."""
    rows, cur = [], None
    for ln in lines:
        m = _LEAD_COUNTY.match(ln) or _LEAD_REGION.match(ln)
        c = _LEAD_CROP.match(ln)
        if m or c or cur is None:
            if cur:
                rows.append(cur)
            cur = {"location": m.group(1) if m else subject_loc, "raw_text": ln,
                   "crop": norm_crop(c.group(1)) if c else None}
        else:
            cur["raw_text"] += " " + ln
    if cur:
        rows.append(cur)
    return rows


def parse_email(subject, body, crop_year, date_reported=None):
    """One of the report emails -> observation rows, stamped with date_reported.

    A multi-location email ("YIELD: Various Locations") carries one
    "County, ST: ..." line per report and splits like a PDF section. A
    single-report email whose body doesn't open with a location becomes one row
    located from the subject. Crop comes from each report's own words when they
    name one (an email can carry corn and bean reports), else from the subject;
    None when neither says."""
    subject_crop = email_crop(subject)
    low_subj = (subject or "").lower()
    all_silage = "silage" in low_subj and not re.search(r"(?:and|&)\s+silage", low_subj)
    lines = _email_lines(body)
    rows = parse_lines(lines, crop_year, report_source="email", split_reports=False)
    located = [r for r in rows if r.get("state")]
    if located:
        rows = located              # drop greeting lines that preceded the first report
    else:
        loc, st = _subject_location(subject)
        rows = [dict(r, crop_year=crop_year, state=st) for r in _lead_rows(lines, loc)]
    out = []
    for r in rows:
        # a crop header/opener, then the report's own crop word, then the subject's
        known = r.get("crop") if r.get("crop") not in (None, "Unknown") else None
        r["crop"] = known or email_crop("", r["raw_text"]) or subject_crop
        r.update(extract_metrics(r["raw_text"], r["crop"]))
        if r["crop"] is None and (r.get("yield_bpa") or 0) > SOY_MAX:
            # no crop named anywhere ("Lee Co IA - 245 bpa, same as last year"), but
            # only corn runs past 100 bpa: corn, and the row says why. At 100 or
            # under either crop is possible, so it waits on Review & edit.
            r["crop"] = "Corn"
            r.update(extract_metrics(r["raw_text"], "Corn"))
            r["notes"] = "Crop from the yield: none named, and over 100 bpa is corn."
        for x in by_entry(r):       # several fields, or dryland and irrigated: a row each
            if all_silage:          # "YIELD: NC IA silage numbers"
                x["is_silage"] = True
            x["dedup_hash"] = row_hash(x)
            x["report_source"] = "email"
            x["source_file"] = None
            x["date_reported"] = date_reported
            x["email_subject"] = (subject or "").strip() or None
            out.append(x)
    return out


# --- forwards -----------------------------------------------------------------
# A colleague's forward of one of the emails (2024's came through a farmer's
# Gmail): the innermost "From: ...@agtradertalk..." header block holds the
# original send time and subject, and the report follows it.
_FWD_FROM = re.compile(r"^\s*From:.*agtradertalk", re.I)
_FWD_HDR = re.compile(r"^\s*(Date|Sent|To|Cc|Subject):\s*(.*)$", re.I)
_FWD_ZONE = re.compile(r"\s+(?:[ECMP][SD]T|UTC|GMT)$")
_FWD_DATES = ("%a, %b %d, %Y %I:%M %p",         # Gmail: "Wed, Sep 11, 2024 at 8:50 AM"
              "%A, %B %d, %Y %I:%M %p",         # Outlook: "Wednesday, September 11, 2024 10:15 AM"
              "%B %d, %Y %I:%M:%S %p")          # iPhone: "September 12, 2024 at 1:48:08 PM CDT"


def _forward_date(text):
    s = re.sub(r"\s+", " ", (text or "").replace(" ", " ")).replace(" at ", " ").strip()
    s = _FWD_ZONE.sub("", s)
    for fmt in _FWD_DATES:
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def unwrap_forward(body):
    """A forwarded copy of one of the emails -> (subject, body, sent) as first sent,
    from the innermost "From: ...@agtradertalk..." header block; None when there's
    no such block or its date or subject can't be read. Only for mail from someone
    else: the source's own replies quote his headers too."""
    lines = (body or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    starts = [i for i, ln in enumerate(lines) if _FWD_FROM.match(ln)]
    if not starts:
        return None
    hdr, j = {}, starts[-1] + 1
    while j < len(lines):
        m = _FWD_HDR.match(lines[j])
        if not m:
            break
        hdr[m.group(1).lower()] = m.group(2).strip()
        j += 1
    sent = _forward_date(hdr.get("date") or hdr.get("sent"))
    if not sent or not hdr.get("subject"):
        return None
    return hdr["subject"], "\n".join(ln.strip() for ln in lines[j:]), sent


def main():
    all_obs = []
    for fname, yr in FILES.items():
        p = os.path.join(DL, fname)
        rows = parse_pdf(p, yr)
        all_obs.extend(rows)
        with_loc = sum(1 for o in rows if o["location"])
        with_yld = sum(1 for o in rows if o["yield_bpa"] is not None)
        print(f"{fname}: {len(rows)} observations "
              f"({with_loc} with location, {with_yld} with a yield number)")

    cols = ["crop_year", "crop", "state", "location", "yield_bpa", "yield_min",
            "yield_max", "n_yields", "ly_yield", "expected_yield",
            "aph", "maturity", "irrigation", "disease",
            "is_silage", "is_record", "report_source", "source_file",
            "dedup_hash", "raw_text"]
    with open(os.path.join(HERE, "yield_observations.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for o in all_obs:
            w.writerow({k: o.get(k) for k in cols})
    with open(os.path.join(HERE, "yield_observations.json"), "w",
              encoding="utf-8") as fh:
        json.dump(all_obs, fh, indent=1, default=str)

    print(f"\nTOTAL: {len(all_obs)} observations")
    # crop / state coverage
    from collections import Counter
    crops = Counter(o["crop"] for o in all_obs)
    states = Counter(o["state"] for o in all_obs)
    print("By crop:", dict(crops))
    print("Top states:", dict(states.most_common(12)))
    nostate = sum(1 for o in all_obs if not o["state"])
    print(f"Rows missing state: {nostate}")


if __name__ == "__main__":
    main()
