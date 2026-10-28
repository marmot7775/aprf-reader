"""The APRF report format, per draft-ietf-mailmaint-aprf-00.

Everything that knows field names lives here, so a new draft revision is an
edit to this file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

DRAFT = "draft-ietf-mailmaint-aprf-00"

# Names the draft defines, in display order; providers may add others
PLACEMENT_NAMES = ("inbox", "unwanted")
ENGAGEMENT_NAMES = ("positive", "neutral", "negative")
# sdi_used values that mean no SDI was applied (section 5.2.1), compared on
# the first word without case or trailing punctuation
NO_SDI = ("", "N/A", "N/F")


def uses_sdi(value: str) -> bool:
    first = value.split(" ", 1)[0].rstrip(";,.").upper()
    return first not in NO_SDI


class ReportError(ValueError):
    """The data is not an APRF report this module can read."""


@dataclass(frozen=True)
class Segment:
    # () when the report is flat (no SDI); otherwise up to four values, broad to narrow
    path: tuple[str, ...]
    # Provider-defined names (inbox, unwanted, ...) to bucket upper bounds
    classification: dict[str, int] = field(default_factory=dict)
    engagement: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Report:
    source: str
    dkim_domain: str
    dkim_selector: str
    report_start: int
    report_end: int
    version: str
    sdi_used: str
    contact_info: str
    extra_info: str
    segments: tuple[Segment, ...]

    @property
    def day(self) -> date:
        # Section 8: the report's date is its ending date, in UTC
        return _day(self.report_end)

    @property
    def stream_key(self) -> tuple[str, str, str]:
        return (self.source, name_key(self.dkim_domain), self.dkim_selector.lower())

    @property
    def key(self) -> tuple[str, str, str, date]:
        return (*self.stream_key, self.day)


def name_key(domain: str) -> str:
    """DNS names compare without case, and bücher.example is xn--bcher-kva.example."""
    d = domain.lower()
    # Only plain letters, digits, dots and hyphens; the web page converts the same set
    # shortcut: Python uses IDNA 2003 and browsers UTS 46, which differ on a few letters such as ß
    if d.isascii() or not all(c.isalnum() or c in ".-" for c in d):
        return d
    try:
        return d.encode("idna").decode("ascii")
    except UnicodeError:
        return d


def _day(epoch: int) -> date:
    return datetime.fromtimestamp(epoch, UTC).date()


# JavaScript numbers are exact only up to here; the web page reads the same files
MAX_COUNT = 2**53 - 1


def _int(v: object) -> int | None:
    """An integer, or a float with nothing after the point (100.0); otherwise None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return v if isinstance(v, int) else None


def _counts(value: object, where: str) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ReportError(f"{where} is not an object")
    out = {}
    for name, n in value.items():
        k = _int(n)
        if k is None or k < 0 or k > MAX_COUNT:
            raise ReportError(f"{where}.{name} is not a count: {json.dumps(n, ensure_ascii=False)}")
        out[str(name)] = k
    return out


def _segment(item: object, i: int) -> Segment:
    if not isinstance(item, dict):
        raise ReportError(f"body[{i}] is not an object")
    seg = item.get("segment")
    # Section 5.2.2 shows a string, the samples a list
    if seg is None:
        path: tuple[str, ...] = ()
    elif isinstance(seg, str):
        path = (seg,)
    elif isinstance(seg, list) and all(isinstance(s, str) for s in seg):
        path = tuple(seg)
    else:
        raise ReportError(f"body[{i}].segment is not a string or list of strings")
    return Segment(
        path,
        _counts(item.get("classification"), f"body[{i}].classification"),
        _counts(item.get("engagement"), f"body[{i}].engagement"),
    )


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _report(obj: object) -> Report:
    if not isinstance(obj, dict) or not isinstance(obj.get("header"), dict):
        raise ReportError("no header object")
    h = obj["header"]
    for name in ("dkim_domain", "dkim_selector"):
        if not isinstance(h.get(name), str) or not h[name]:
            raise ReportError(f"header.{name} is missing")
    start, end = _int(h.get("report_start")), _int(h.get("report_end"))
    for name, v in (("report_start", start), ("report_end", end)):
        if v is None:
            raise ReportError(f"header.{name} is not an epoch time")
        # Seconds past 1e11 are beyond the year 5138; up to 1e14 that is milliseconds
        if 10**11 <= abs(v) < 10**14:
            raise ReportError(f"header.{name} looks like milliseconds, not seconds: {v}")
    if end < start:
        raise ReportError("header.report_end is before report_start")
    try:
        _day(end)
    except (OverflowError, OSError, ValueError):
        raise ReportError(f"header.report_end is out of range: {end}") from None
    body = obj.get("body", [])
    if not isinstance(body, list):
        raise ReportError("body is not a list")
    return Report(
        source=_text(h.get("source")) or "unknown",
        dkim_domain=h["dkim_domain"],
        dkim_selector=h["dkim_selector"],
        report_start=start,
        report_end=end,
        version=_text(h.get("version")),
        # Comcast's beta sends " - N/A - Beta - Not in use"
        sdi_used=_text(h.get("sdi_used")).strip(" -"),
        contact_info=_text(h.get("contact_info")),
        extra_info=_text(h.get("extra_info")),
        segments=tuple(_segment(s, i) for i, s in enumerate(body)),
    )


def parse_items(data: bytes) -> list[Report | ReportError]:
    """Each report in a document, or the reason it cannot be read, so one bad
    report does not cost the rest of the file."""
    try:
        doc = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ReportError(f"not JSON: {e}") from None
    except RecursionError:
        raise ReportError("JSON nested too deeply") from None
    items = doc if isinstance(doc, list) else [doc]
    if not items:
        raise ReportError("empty list")
    out: list[Report | ReportError] = []
    for o in items:
        try:
            out.append(_report(o))
        except ReportError as e:
            out.append(e)
    return out


def parse(data: bytes) -> list[Report]:
    """Parse one report document. The draft's samples wrap reports in a list."""
    items = parse_items(data)
    for r in items:
        if isinstance(r, ReportError):
            raise r
    return items  # type: ignore[return-value]
