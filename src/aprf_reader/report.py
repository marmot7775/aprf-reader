"""The APRF report format, per draft-ietf-mailmaint-aprf-00.

Everything that knows field names lives here, so a new draft revision is an
edit to this file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

DRAFT = "draft-ietf-mailmaint-aprf-00"


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
        return datetime.fromtimestamp(self.report_end, UTC).date()

    @property
    def key(self) -> tuple[str, str, str, date]:
        return (self.source, self.dkim_domain.lower(), self.dkim_selector, self.day)


def _counts(value: object, where: str) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ReportError(f"{where} is not an object")
    out = {}
    for name, n in value.items():
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise ReportError(f"{where}.{name} is not a count: {n!r}")
        out[str(name)] = n
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
    for name in ("report_start", "report_end"):
        if isinstance(h.get(name), bool) or not isinstance(h.get(name), int):
            raise ReportError(f"header.{name} is not an epoch time")
    body = obj.get("body", [])
    if not isinstance(body, list):
        raise ReportError("body is not a list")
    return Report(
        source=_text(h.get("source")) or "unknown",
        dkim_domain=h["dkim_domain"],
        dkim_selector=h["dkim_selector"],
        report_start=h["report_start"],
        report_end=h["report_end"],
        version=_text(h.get("version")),
        sdi_used=_text(h.get("sdi_used")),
        contact_info=_text(h.get("contact_info")),
        extra_info=_text(h.get("extra_info")),
        segments=tuple(_segment(s, i) for i, s in enumerate(body)),
    )


def parse(data: bytes) -> list[Report]:
    """Parse one report document. The draft's samples wrap reports in a list."""
    try:
        doc = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ReportError(f"not JSON: {e}") from None
    items = doc if isinstance(doc, list) else [doc]
    if not items:
        raise ReportError("empty list")
    return [_report(o) for o in items]
