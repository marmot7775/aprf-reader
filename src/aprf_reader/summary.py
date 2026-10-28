"""Group reports into streams: one provider, signing domain and selector over time."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from functools import cached_property

from .report import ENGAGEMENT_NAMES, NO_SDI, PLACEMENT_NAMES, Report


@dataclass(frozen=True)
class Row:
    day: date
    # For a gap, the last missing day of the run; equal to day otherwise
    until: date
    # None for a run of days with no report
    segment: str | None
    values: tuple[int | None, ...] = ()


@dataclass
class Stream:
    source: str
    domain: str
    selector: str
    by_day: dict[date, Report]

    @cached_property
    def first(self) -> date:
        return min(self.by_day)

    @cached_property
    def last(self) -> date:
        return max(self.by_day)

    @cached_property
    def missing(self) -> int:
        return (self.last - self.first).days + 1 - len(self.by_day)

    def _names(self, attr: str, order: tuple[str, ...]) -> list[str]:
        seen = {k for r in self.by_day.values() for s in r.segments for k in getattr(s, attr)}
        return [k for k in order if k in seen] + sorted(seen - set(order))

    @cached_property
    def columns(self) -> list[tuple[str, str]]:
        """(attribute, name) pairs: placement counts, then engagement counts."""
        return ([("classification", k) for k in self._names("classification", PLACEMENT_NAMES)]
                + [("engagement", k) for k in self._names("engagement", ENGAGEMENT_NAMES)])

    @cached_property
    def rows(self) -> list[Row]:
        """One row per segment per report day; one row per run of days with no report."""
        out: list[Row] = []
        prev: date | None = None
        for d in sorted(self.by_day):
            if prev is not None and (d - prev).days > 1:
                out.append(Row(prev + timedelta(1), d - timedelta(1), None))
            for s in self.by_day[d].segments:
                vals = tuple(getattr(s, a).get(k) for a, k in self.columns)
                out.append(Row(d, d, " / ".join(s.path) or "all mail", vals))
            prev = d
        return out

    def trend(self, col: int) -> list[tuple[int, int]]:
        """(day offset from first, sum across segments) for each report day."""
        a, k = self.columns[col]
        return [((d - self.first).days, sum(getattr(s, a).get(k, 0) for s in r.segments))
                for d, r in sorted(self.by_day.items())]

    @cached_property
    def notes(self) -> list[str]:
        out = []
        prev: str | None = None
        for d, r in sorted(self.by_day.items()):
            if prev is not None and r.version != prev:
                out.append(f"Report version changed from {prev} to {r.version} on {d}: "
                           "the provider may have changed what a category counts.")
            prev = r.version
        # "TBD" is the draft samples' placeholder
        out += [f"Provider note: {t}" for t in sorted({r.extra_info for r in self.by_day.values()} - {"", "TBD"})]
        sdi = sorted({r.sdi_used for r in self.by_day.values()} - set(NO_SDI))
        if sdi:
            out.append("SDI used: " + ", ".join(sdi))
        return out


def streams(reports: list[Report]) -> list[Stream]:
    groups: dict[tuple[str, str, str], dict[date, Report]] = defaultdict(dict)
    for r in reports:
        groups[(r.source, r.dkim_domain.lower(), r.dkim_selector)][r.day] = r
    return [Stream(src, dom, sel, days) for (src, dom, sel), days in sorted(groups.items())]
