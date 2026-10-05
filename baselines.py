"""
County baselines where NASS skipped the county (spec 01b). NASS publishes
fewer counties every year (1,512 corn counties in 2022, 1,211 in 2025), so a
report's county can lack the figure a baseline needs. Rather than drop
straight to the state, which washes out why the county is different:

  county          the county's own NASS figure
  own trend       a one-year gap in a county NASS otherwise publishes: its own
                  linear trend over its other years (a county predicts itself
                  better than its neighbors do)
  neighbors       the counties around it that have the figure, weighted by
                  closeness (1 / distance between centre points): the bordering
                  ring first, widening to the second and third rings while
                  fewer than 3 have it; at least 2, or no neighbor baseline
  state fallback  none of those: the state's figure, flagged and kept out of
                  the headline medians (analysis.summarize)

Same crop only; a state line is no barrier when the counties border. Which
neighbors went into a figure isn't stored, only the source and the count.
"""
import math

RINGS = 3
WANT_NEIGHBORS = 3     # widen to the next ring while fewer than this have the figure
MIN_NEIGHBORS = 2
MIN_TREND_YEARS = 5
RANK = {"county": 0, "own trend": 1, "neighbors": 2}


def _km(a, b):
    """Great-circle distance between two (lat, lon) points."""
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    h = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _ok(v):
    return v is not None and v == v


class Geo:
    """County centre points and shared borders, from geo.load()."""

    def __init__(self, frame=None):
        self.pos, self.adj = {}, {}
        if frame is not None and len(frame):
            for r in frame.itertuples(index=False):
                self.pos[r.fips] = (float(r.lat), float(r.lon))
                # an island county has none; the database hands that back as NULL
                self.adj[r.fips] = set(r.neighbors.split() if isinstance(r.neighbors, str) else ())

    def __bool__(self):
        return bool(self.pos)


def neighbor_value(geo, values: dict, fips: str):
    """Inverse-distance mean of a figure over the counties around `fips`
    (`values`: fips -> figure). -> (value or None, contributing count)."""
    if not geo or fips not in geo.pos:
        return None, 0
    seen, frontier, got = {fips}, {fips}, []
    for _ in range(RINGS):
        ring = set().union(*(geo.adj.get(f, set()) for f in frontier)) - seen
        if not ring:
            break
        seen |= ring
        frontier = ring
        got += [(nb, float(values[nb])) for nb in sorted(ring)
                if nb in geo.pos and _ok(values.get(nb))]
        if len(got) >= WANT_NEIGHBORS:
            break
    if len(got) < MIN_NEIGHBORS:
        return None, len(got)
    here = geo.pos[fips]
    weights = [(1 / max(_km(here, geo.pos[nb]), 1.0), v) for nb, v in got]
    return sum(w * v for w, v in weights) / sum(w for w, _ in weights), len(got)


def own_trend(finals: dict, year: int):
    """The county's straight-line trend over its other finals, at `year`. Only
    for a gap: a final the year before or after, and MIN_TREND_YEARS in all."""
    pts = [(y, v) for y, v in finals.items() if y != year and _ok(v)]
    if len(pts) < MIN_TREND_YEARS or not ({year - 1, year + 1} & {y for y, _ in pts}):
        return None
    n = len(pts)
    mx, my = sum(y for y, _ in pts) / n, sum(v for _, v in pts) / n
    sxx = sum((y - mx) ** 2 for y, _ in pts)
    slope = sum((y - mx) * (v - my) for y, v in pts) / sxx if sxx else 0.0
    return my + slope * (year - mx)


class CountyBaselines:
    """The county table (nass.county_table: crop, fips, year -> final, ly, avg5)
    with the chain above for a county that lacks a figure."""

    def __init__(self, county_tbl, geo):
        self.geo = geo
        self.rows, self.finals, self.by_year = {}, {}, {}
        for r in county_tbl.itertuples(index=False):
            y = int(r.year)
            self.rows[(r.crop, r.fips, y)] = r
            self.by_year.setdefault((r.crop, y), []).append(r)
            if _ok(r.final):
                self.finals.setdefault((r.crop, r.fips), {})[y] = float(r.final)
        self._values = {}

    def values(self, crop, year, col):
        """fips -> figure for every county that has it, one crop and year."""
        key = (crop, year, col)
        if key not in self._values:
            self._values[key] = {r.fips: getattr(r, col) for r in self.by_year.get((crop, year), [])
                                 if _ok(getattr(r, col))}
        return self._values[key]

    def get(self, crop, fips, year, col):
        """One county's figure ('final', 'ly' or 'avg5') for `year`.
        -> (value, source) with source 'county', 'own trend' or 'neighbors';
        (None, None) when none of them has it."""
        r = self.rows.get((crop, fips, year))
        if r is not None and _ok(getattr(r, col)):
            return float(getattr(r, col)), "county"
        if col in ("final", "ly"):
            t = own_trend(self.finals.get((crop, fips), {}), year if col == "final" else year - 1)
            if t is not None:
                return t, "own trend"
        v, _n = neighbor_value(self.geo, self.values(crop, year, col), fips)
        return (v, "neighbors") if v is not None else (None, None)

    def combined(self, crop, fips_list, year, col):
        """A report naming several counties ("Moultrie/Coles Co"): the mean of
        those that have a figure, tagged with the weakest source among them."""
        got = [self.get(crop, f, year, col) for f in fips_list]
        vals = [(v, s) for v, s in got if v is not None]
        if not vals:
            return None, None
        return (sum(v for v, _ in vals) / len(vals),
                max((s for _, s in vals), key=RANK.get))
