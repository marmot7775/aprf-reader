"""Write a stream summary as Markdown or as a self-contained HTML page."""

from __future__ import annotations

import html
import re
from datetime import date

from . import __version__
from .load import Loaded
from .report import DRAFT
from .summary import Row, Stream, streams

NO_COUNTS = "These reports carry no counts."

CAVEAT = (
    "Counts are bucket upper bounds chosen by each provider: a ceiling, not an exact figure. "
    "Placement counts mail received that day; engagement counts actions taken that day on mail "
    "from any day. Dividing one count by another gives a ratio nobody measured."
)


def _n(v: int | None) -> str:
    return "" if v is None else f"{v:,}"


def _md(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _counts(loaded: Loaded, ss: list[Stream]) -> str:
    return f"{_plural(len(loaded.reports), 'report')} in {_plural(len(ss), 'stream')}."


def _span(s: Stream) -> str:
    gap = f", {_plural(s.missing, 'day')} with no report" if s.missing else ""
    days = str(s.first) if s.first == s.last else f"{s.first} to {s.last}"
    return f"{days}: {_plural(len(s.by_day), 'report')}{gap}"


def _day(row: Row) -> str:
    return str(row.day) if row.until == row.day else f"{row.day} to {row.until}"


def to_markdown(loaded: Loaded) -> str:
    ss = streams(loaded.reports)
    lines = ["# APRF placement summary", "", f"{_counts(loaded, ss)} {CAVEAT}", ""]
    for s in ss:
        lines += [f"## {_md(s.source)}: {_md(s.domain)}, selector {_md(s.selector)}", "", _span(s), ""]
        cols = [k for _, k in s.columns]
        if not cols:
            lines += [NO_COUNTS, ""]
            continue
        lines.append("| Day | Segment | " + " | ".join(_md(c) for c in cols) + " |")
        lines.append("|---|---|" + "---:|" * len(cols))
        for row in s.rows:
            if row.segment is None:
                lines.append(f"| {_day(row)} | no report |" + " |" * len(cols))
            else:
                lines.append(f"| {row.day} | {_md(row.segment)} | " + " | ".join(_n(v) for v in row.values) + " |")
        lines.append("")
        if s.changes:
            lines += ["Changes worth a look:", ""] + [f"- {_md(c)}" for c in s.changes] + [""]
        if s.notes:
            lines += [f"- {_md(n)}" for n in s.notes] + [""]
    if loaded.skipped:
        lines += ["## Skipped", ""] + [f"- {_md(w)}: {_md(r)}" for w, r in loaded.skipped] + [""]
    lines.append(f"Read by aprf-reader {__version__} against {DRAFT}.")
    return "\n".join(lines) + "\n"


def _spark(points: list[tuple[int, int]]) -> str:
    """Line over (day offset, value) points, broken where days are missing."""
    if len(points) < 2:
        return ""
    w, h = 96, 20
    top = max(v for _, v in points) or 1
    step = w / points[-1][0]
    path, prev = [], None
    for x, v in points:
        cmd = "L" if prev is not None and x - prev == 1 else "M"
        path.append(f"{cmd}{x * step:.1f},{h - 1 - (v / top) * (h - 2):.1f}")
        prev = x
    return (f'<svg class="spark" viewBox="-1 0 {w + 2} {h}" width="{w}" height="{h}" aria-hidden="true">'
            f'<path d="{" ".join(path)}" fill="none" stroke="currentColor" stroke-width="1.5" '
            'stroke-linecap="round"/></svg>')


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
        cols = [k for _, k in s.columns]
        parts.append(f"<h2>{e(s.source)}: {e(s.domain)}, selector {e(s.selector)}</h2>")
        if not cols:
            parts.append(f"<p class=muted>{e(_span(s))}</p><p>{NO_COUNTS}</p>")
            continue
        parts.append(f"<p class=muted>{e(_span(s))}</p><div class=wrap><table><thead><tr><th>Day<th>Segment")
        parts.append("".join(f"<th>{e(c)}" for c in cols) + "</thead><tbody>")
        parts.append("<tr class=trend><td>Trend<td class=muted>sum per day")
        parts.append("".join(f"<td>{_spark(s.trend(i))}" for i in range(len(cols))))
        for row in s.rows:
            if row.segment is None:
                parts.append(f"<tr class=gap><td>{_day(row)}<td>no report" + "<td>" * len(cols))
            else:
                parts.append(f"<tr><td>{row.day}<td>{e(row.segment)}" + "".join(f"<td>{_n(v)}" for v in row.values))
        parts.append("</tbody></table></div>")
        if s.changes:
            parts.append("<p>Changes worth a look:</p><ul>" + "".join(f"<li>{e(c)}" for c in s.changes) + "</ul>")
        if s.notes:
            parts.append("<ul>" + "".join(f"<li>{e(n)}" for n in s.notes) + "</ul>")
    if loaded.skipped:
        parts.append("<h2>Skipped</h2><ul>" + "".join(f"<li>{e(w)}: {e(r)}" for w, r in loaded.skipped) + "</ul>")
    parts.append(f"<p class=muted>Read by aprf-reader {e(__version__)} against {DRAFT}. Nothing left this computer.</p>")
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"


# ---- terminal view ----

BARS = "▁▂▃▄▅▆▇█"


class _Style:
    def __init__(self, color: bool):
        self.on = color

    def __call__(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.on else text

    def bold(self, t: str) -> str: return self("1", t)
    def dim(self, t: str) -> str: return self("2", t)
    def accent(self, t: str) -> str: return self("1;34", t)
    def warn(self, t: str) -> str: return self("33", t)
    def good(self, t: str) -> str: return self("32", t)


def _bars(points: list[tuple[int, int]], width: int) -> str:
    """One character per day, a space for a day with no report; days share a cell when they do not fit."""
    span = points[-1][0] + 1
    cells: list[int | None] = [None] * min(span, width)
    for x, v in points:
        i = x * len(cells) // span
        cells[i] = v if cells[i] is None else max(cells[i], v)
    top = max(v for _, v in points) or 1
    if min(v for _, v in points) == top:
        # A flat line is the same every day; a full bar would read as a peak
        return "".join(" " if v is None else BARS[3] for v in cells)
    return "".join(" " if v is None else BARS[min(7, v * 7 // top)] if v else BARS[0] for v in cells)


def _spread(points: list[tuple[int, int]]) -> str:
    lo, hi = min(v for _, v in points), max(v for _, v in points)
    if lo == hi:
        return f"{lo:,}" if len(points) == 1 else f"{lo:,} every day"
    return f"{lo:,} to {hi:,}"


def _date(d) -> str:
    return f"{d:%b} {d.day}"


def _range(a, b) -> str:
    if a == b:
        return f"{_date(a)}, {a.year}"
    return (_date(a) if a.year == b.year else f"{_date(a)}, {a.year}") + f" to {_date(b)}, {b.year}"


def _wrap(text: str, width: int, indent: str = "") -> list[str]:
    import textwrap
    return textwrap.wrap(text, width=max(40, width), initial_indent=indent, subsequent_indent=indent) or [indent]


def to_terminal(loaded: Loaded, color: bool = True, width: int = 100) -> str:
    """A readable overview for a terminal: per stream, each count's latest value, range and trend."""
    st = _Style(color)
    ss = streams(loaded.reports)
    out = [st.bold("APRF placement summary") + st.dim(f"  {_counts(loaded, ss)}"), ""]
    out += [st.dim(line) for line in _wrap(CAVEAT, min(width, 100))] + [""]
    for s in ss:
        out.append(st.accent(s.domain) + "  " + st.bold(f"selector {s.selector}"))
        meta = [s.source, _range(s.first, s.last), _plural(len(s.by_day), "report")]
        line = st.dim(" · ".join(meta))
        if s.missing:
            line += st.dim(" · ") + st.warn(f"{_plural(s.missing, 'day')} with no report")
        out += [line, ""]
        names = [k for _, k in s.columns]
        if not names:
            out += ["  " + st.dim(NO_COUNTS), ""]
            continue
        nw = max([len(n) for n in names] + [5])
        trends = [s.trend(i) for i in range(len(names))]
        latest = [f"{t[-1][1]:,}" for t in trends]
        ranges = [_spread(t) for t in trends]
        lw, rw = max(len(x) for x in latest + ["latest"]), max(len(x) for x in ranges + ["range"])
        bw = max(10, min(60, width - (2 + nw + 2 + lw + 2 + rw + 2)))
        out.append(st.dim(f"  {'':<{nw}}  {'latest':>{lw}}  {'range':<{rw}}  trend"))
        for name, t, l, r in zip(names, trends, latest, ranges):
            out.append(f"  {name:<{nw}}  {st.bold(f'{l:>{lw}}')}  {r:<{rw}}  {st.accent(_bars(t, bw)) if len(t) > 1 else st.dim('one day')}")
        out.append("")
        if s.changes:
            out.append(st.bold("  Changes worth a look"))
            out += [f"  {st.warn('!')} " + re.sub(r"(\d{4})-(\d\d)-(\d\d)", lambda m: _date(date.fromisoformat(m[0])), c)
                    for c in s.changes]
        else:
            out.append(f"  {st.good('✓')} " + st.dim("No big day-to-day moves in inbox, unwanted, or negative counts."))
        for n in s.notes:
            out += _wrap(n, width - 4, "  · ")
        out.append("")
    if loaded.skipped:
        out.append(st.warn(f"Skipped {len(loaded.skipped)}"))
        for w, r in loaded.skipped:
            out += [st.dim(line) for line in _wrap(f"{w}: {r}", width - 2, "  ")]
        out.append("")
    out.append(st.dim(f"aprf-reader {__version__}, {DRAFT}. Daily tables: --markdown, or --html for a page."))
    return "\n".join(out) + "\n"
