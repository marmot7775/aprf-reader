"""Group reports into streams and write Markdown and HTML summaries."""

from __future__ import annotations

import html
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from . import __version__
from .load import Loaded
from .report import DRAFT, Report

# Draft names first in a fixed order; anything else a provider adds follows, sorted
PLACEMENT_ORDER = ("inbox", "unwanted")
ENGAGEMENT_ORDER = ("positive", "neutral", "negative")

CAVEAT = (
    "Counts are bucket upper bounds chosen by each provider: a ceiling, not an exact figure. "
    "Placement counts mail received that day; engagement counts actions taken that day on mail "
    "from any day. Dividing one count by another gives a ratio nobody measured."
)


@dataclass
class Stream:
    source: str
    domain: str
    selector: str
    by_day: dict[date, Report]

    @property
    def days(self) -> list[date]:
        first, last = min(self.by_day), max(self.by_day)
        return [first + timedelta(n) for n in range((last - first).days + 1)]

    @property
    def missing(self) -> int:
        return len(self.days) - len(self.by_day)

    def _keys(self, attr: str, order: tuple[str, ...]) -> list[str]:
        seen = {k for r in self.by_day.values() for s in r.segments for k in getattr(s, attr)}
        return [k for k in order if k in seen] + sorted(seen - set(order))

    @property
    def placement(self) -> list[str]:
        return self._keys("classification", PLACEMENT_ORDER)

    @property
    def engagement(self) -> list[str]:
        return self._keys("engagement", ENGAGEMENT_ORDER)

    def rows(self) -> list[tuple[date, str, list[int | None]] | tuple[date, None, None]]:
        """One row per segment per day; (day, None, None) for a day with no report."""
        out: list = []
        for d in self.days:
            r = self.by_day.get(d)
            if r is None:
                out.append((d, None, None))
                continue
            for s in r.segments:
                vals = [s.classification.get(k) for k in self.placement] + [s.engagement.get(k) for k in self.engagement]
                out.append((d, " / ".join(s.path) or "all mail", vals))
        return out

    def totals(self, d: date) -> list[int | None]:
        """Per-day sum across segments, for the trend line. None when the day had no report."""
        r = self.by_day.get(d)
        if r is None:
            return [None] * (len(self.placement) + len(self.engagement))
        cols = [("classification", k) for k in self.placement] + [("engagement", k) for k in self.engagement]
        return [sum(getattr(s, a).get(k, 0) for s in r.segments) for a, k in cols]

    def notes(self) -> list[str]:
        out = []
        prev: str | None = None
        for d in sorted(self.by_day):
            v = self.by_day[d].version
            if prev is not None and v != prev:
                out.append(f"Report version changed from {prev} to {v} on {d}: the provider may have changed what a category counts.")
            prev = v
        for text in sorted({r.extra_info for r in self.by_day.values()} - {"", "TBD"}):
            out.append(f"Provider note: {text}")
        sdi = sorted({r.sdi_used for r in self.by_day.values()} - {"", "N/A", "N/F"})
        if sdi:
            out.append("SDI used: " + ", ".join(sdi))
        return out


def streams(reports: list[Report]) -> list[Stream]:
    groups: dict[tuple[str, str, str], dict[date, Report]] = defaultdict(dict)
    for r in reports:
        groups[(r.source, r.dkim_domain.lower(), r.dkim_selector)][r.day] = r
    return [Stream(src, dom, sel, days) for (src, dom, sel), days in sorted(groups.items())]


def _n(v: int | None) -> str:
    return "" if v is None else f"{v:,}"


def _md(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _counts(loaded: Loaded, ss: list[Stream]) -> str:
    return f"{_plural(len(loaded.reports), 'report')} in {_plural(len(ss), 'stream')}."


def _span(s: Stream) -> str:
    days = s.days
    gap = f", {_plural(s.missing, 'day')} with no report" if s.missing else ""
    return f"{days[0]} to {days[-1]}: {_plural(len(s.by_day), 'report')}{gap}"


def to_markdown(loaded: Loaded) -> str:
    ss = streams(loaded.reports)
    lines = ["# APRF placement summary", "", f"{_counts(loaded, ss)} {CAVEAT}", ""]
    for s in ss:
        lines += [f"## {_md(s.source)}: {_md(s.domain)}, selector {_md(s.selector)}", "", _span(s), ""]
        cols = s.placement + s.engagement
        lines.append("| Day | Segment | " + " | ".join(_md(c) for c in cols) + " |")
        lines.append("|---|---|" + "---:|" * len(cols))
        for d, seg, vals in s.rows():
            if seg is None:
                lines.append(f"| {d} | no report |" + " |" * len(cols))
            else:
                lines.append(f"| {d} | {_md(seg)} | " + " | ".join(_n(v) for v in vals) + " |")
        lines.append("")
        lines += [f"- {_md(n)}" for n in s.notes()]
        if s.notes():
            lines.append("")
    if loaded.skipped:
        lines += ["## Skipped", ""] + [f"- {_md(w)}: {_md(r)}" for w, r in loaded.skipped] + [""]
    lines.append(f"Read by aprf-reader {__version__} against {DRAFT}.")
    return "\n".join(lines) + "\n"


def _spark(values: list[int | None]) -> str:
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(pts) < 2:
        return ""
    w, h = 96, 20
    top = max(v for _, v in pts) or 1
    step = w / (len(values) - 1)
    xy = " ".join(f"{i * step:.1f},{h - 1 - (v / top) * (h - 2):.1f}" for i, v in pts)
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true">'
            f'<polyline points="{xy}" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>')


CSS = """
:root{--bg:#fff;--fg:#1a1a1a;--muted:#5f6368;--line:#dadce0;--gap:#f6f6f6;--accent:#1a5fb4}
@media (prefers-color-scheme:dark){:root{--bg:#161616;--fg:#e8e8e8;--muted:#a0a0a0;--line:#3a3a3a;--gap:#202020;--accent:#78aeed}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:960px;padding:24px 16px}
h1{font-size:1.5rem}h2{font-size:1.15rem;margin-top:2rem}
.muted{color:var(--muted)}
.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:4px 8px;text-align:right;white-space:nowrap}
th:nth-child(-n+2),td:nth-child(-n+2){text-align:left}
tr.gap td{background:var(--gap);color:var(--muted)}tr.trend td{border-bottom:2px solid var(--line)}
.spark{color:var(--accent);vertical-align:middle}
"""


def to_html(loaded: Loaded) -> str:
    e = html.escape
    ss = streams(loaded.reports)
    parts = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        '<meta name=viewport content="width=device-width,initial-scale=1">',
        f"<title>APRF placement summary</title><style>{CSS}</style></head><body>",
        "<h1>APRF placement summary</h1>",
        f"<p>{_counts(loaded, ss)}</p><p class=muted>{e(CAVEAT)}</p>",
    ]
    for s in ss:
        cols = s.placement + s.engagement
        parts.append(f"<h2>{e(s.source)}: {e(s.domain)}, selector {e(s.selector)}</h2>")
        parts.append(f"<p class=muted>{e(_span(s))}</p><div class=wrap><table><thead><tr><th>Day<th>Segment")
        parts.append("".join(f"<th>{e(c)}" for c in cols) + "</thead><tbody>")
        daily = [s.totals(d) for d in s.days]
        parts.append("<tr class=trend><td>Trend<td class=muted>sum per day")
        parts.append("".join(f"<td>{_spark([row[i] for row in daily])}" for i in range(len(cols))))
        for d, seg, vals in s.rows():
            if seg is None:
                parts.append(f"<tr class=gap><td>{d}<td>no report" + "<td>" * len(cols))
            else:
                parts.append(f"<tr><td>{d}<td>{e(seg)}" + "".join(f"<td>{_n(v)}" for v in vals))
        parts.append("</tbody></table></div>")
        if s.notes():
            parts.append("<ul>" + "".join(f"<li>{e(n)}" for n in s.notes()) + "</ul>")
    if loaded.skipped:
        parts.append("<h2>Skipped</h2><ul>" + "".join(f"<li>{e(w)}: {e(r)}" for w, r in loaded.skipped) + "</ul>")
    parts.append(f"<p class=muted>Read by aprf-reader {e(__version__)} against {DRAFT}. Nothing left this computer.</p>")
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"
