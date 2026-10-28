"""The APRF report format, per draft-ietf-mailmaint-aprf-00.

Everything that knows field names lives here, so a new draft revision is an
edit to this file.
"""

from __future__ import annotations

import json
import re
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


DAY = 86400
ONE_DAY = "not one UTC day from 00:00:00 to 23:59:59, which draft section 5.1 requires"
# Placement names section 6 gives as examples; engagement names section 5.2.2 lists
NAMED_CLASSIFICATION = ("inbox", "unwanted", "forwarded", "promotional")
# sdi as published in DNS (section 4): a header field name, a comma, one separator character
SDI_FORMAT = re.compile(r"[!-9;-~]+,\S")


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
    # (warning, detail) where the report departs from the draft but can still be read
    warnings: tuple[tuple[str, str], ...] = ()

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


def _counts(value: object, where: str, section: str) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ReportError(f"{where} is not an object ({section})")
    out = {}
    for name, n in value.items():
        k = _int(n)
        if k is None or k < 0 or k > MAX_COUNT:
            raise ReportError(f"{where}.{name} is not a count ({section}): {json.dumps(n, ensure_ascii=False, separators=(',', ':'))}")
        out[str(name)] = k
    return out


def _segment(item: object, i: int) -> Segment:
    if not isinstance(item, dict):
        raise ReportError(f"body[{i}] is not an object (section 5.2.2)")
    seg = item.get("segment")
    # Section 5.2.2 shows a string, the samples a list
    if seg is None:
        path: tuple[str, ...] = ()
    elif isinstance(seg, str):
        path = (seg,)
    elif isinstance(seg, list) and all(isinstance(s, str) for s in seg):
        path = tuple(seg)
    else:
        raise ReportError(f"body[{i}].segment is not a string or list of strings (section 5.2.2)")
    return Segment(
        path,
        _counts(item.get("classification"), f"body[{i}].classification", "section 6"),
        _counts(item.get("engagement"), f"body[{i}].engagement", "section 7"),
    )


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _report(obj: object) -> Report:
    if not isinstance(obj, dict) or not isinstance(obj.get("header"), dict):
        raise ReportError("no header object (section 5.2.1)")
    h = obj["header"]
    for name in ("dkim_domain", "dkim_selector"):
        if not isinstance(h.get(name), str) or not h[name]:
            raise ReportError(f"header.{name} is missing (section 5.2.1)")
    start, end = _int(h.get("report_start")), _int(h.get("report_end"))
    for name, v in (("report_start", start), ("report_end", end)):
        if v is None:
            raise ReportError(f"header.{name} is not an epoch time (section 5.2.1)")
        # Seconds past 1e11 are beyond the year 5138; up to 1e14 that is milliseconds
        if 10**11 <= abs(v) < 10**14:
            raise ReportError(f"header.{name} looks like milliseconds, not epoch seconds (section 5.2.1): {v}")
    if end < start:
        raise ReportError("header.report_end is before report_start (section 5.1)")
    try:
        _day(end)
    except (OverflowError, OSError, ValueError):
        raise ReportError(f"header.report_end is out of range (section 5.2.1): {end}") from None
    if start % DAY or end != start + DAY - 1:
        raise ReportError(ONE_DAY)
    body = obj.get("body", [])
    if not isinstance(body, list):
        raise ReportError("body is not a list of segments (section 5.2.2)")
    segments = tuple(_segment(s, i) for i, s in enumerate(body))
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
        segments=segments,
        warnings=_warnings(h, segments),
    )


def _warnings(h: dict, segments: tuple[Segment, ...]) -> tuple[tuple[str, str], ...]:
    out = []
    if "sdi_used" not in h:
        out.append(("no sdi_used field; section 5.2.1 requires N/A when no SDI is used", ""))
    else:
        raw = h["sdi_used"]
        if not (raw in ("N/A", "N/F") or (isinstance(raw, str) and SDI_FORMAT.fullmatch(raw))):
            out.append(("sdi_used is not N/A, N/F, or the sdi value as published in DNS (section 5.2.1)",
                        json.dumps(raw, ensure_ascii=False, separators=(',', ':'))))
    keys = lambda attr: sorted({k for s in segments for k in getattr(s, attr)})
    for k in keys("classification"):
        if k not in NAMED_CLASSIFICATION:
            out.append((f"classification key {json.dumps(k, ensure_ascii=False)} is not one section 6 names "
                        "(inbox, unwanted, forwarded, promotional); read as given", ""))
    for k in keys("engagement"):
        if k not in ENGAGEMENT_NAMES:
            out.append((f"engagement key {json.dumps(k, ensure_ascii=False)} is not positive, neutral or "
                        "negative (section 5.2.2); read as given", ""))
    return tuple(out)


def _no_constant(name: str) -> None:
    # Python's json takes NaN and Infinity; RFC 8259 and browsers do not
    raise json.JSONDecodeError(f"{name} is not valid JSON", name, 0)


def parse_items(data: bytes) -> list[Report | ReportError]:
    """Each report in a document, or the reason it cannot be read, so one bad
    report does not cost the rest of the file."""
    try:
        doc = json.loads(data, parse_constant=_no_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ReportError(f"not valid JSON, which section 5 requires: {e}") from None
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
