"""Group reports into streams: one provider, signing domain and selector over time."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from functools import cached_property

from .report import ENGAGEMENT_NAMES, PLACEMENT_NAMES, Report, uses_sdi


# Counts where a jump up is bad news, or a drop is
CHANGES = {"unwanted": "rise", "negative": "rise", "inbox": "fall"}
MIN_MOVE = 200


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
                out.append(Row(d, d, " / ".join(p or "(blank)" for p in s.path) or "all mail", vals))
            prev = d
        return out

    def trend(self, col: int) -> list[tuple[int, int]]:
        """(day offset from first, sum across segments) for each report day."""
        a, k = self.columns[col]
        return [((d - self.first).days, sum(getattr(s, a).get(k, 0) for s in r.segments))
                for d, r in sorted(self.by_day.items())]

    @cached_property
    def changes(self) -> list[str]:
        """Day-to-day moves big enough to look at, between one report day and the next."""
        out = []
        for col, (_, k) in enumerate(self.columns):
            rule = CHANGES.get(k)
            if rule is None:
                continue
            pts = self.trend(col)
            for (x0, old), (x1, new) in zip(pts, pts[1:]):
                # Comcast's buckets are 100 wide, so a move under two buckets is noise
                if rule == "rise" and new >= 2 * old and new - old >= MIN_MOVE:
                    verb = "rose"
                elif rule == "fall" and 2 * new <= old and old - new >= MIN_MOVE:
                    verb = "fell"
                else:
                    continue
                d0, d1 = self.first + timedelta(x0), self.first + timedelta(x1)
                out.append(f"{k} {verb} from {old:,} on {d0} to {new:,} on {d1}.")
        return sorted(out, key=lambda t: t.rsplit(" on ", 1)[1])

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
        sdi = sorted({r.sdi_used for r in self.by_day.values() if uses_sdi(r.sdi_used)})
        if sdi:
            out.append("SDI used: " + ", ".join(sdi))
        return out


def streams(reports: list[Report]) -> list[Stream]:
    groups: dict[tuple[str, str, str], dict[date, Report]] = defaultdict(dict)
    for r in reports:
        groups[r.stream_key][r.day] = r
    return [Stream(src, dom, sel, days) for (src, dom, sel), days in sorted(groups.items())]


def grouped(warnings: list[tuple[str, str, str]]) -> list[tuple[str, int, str, str]]:
    """(warning, times, first where, first detail), in the order first seen."""
    out: dict[str, list] = {}
    for w, d, where in warnings:
        if w in out:
            out[w][1] += 1
        else:
            out[w] = [w, 1, where, d]
    return [tuple(v) for v in out.values()]


def copies_line(n: int) -> str:
    return (f"{n} file{'s' if n != 1 else ''} repeated a report already read, such as the same report "
            "saved as .json and as .eml, and counted once.")


def to_dict(loaded) -> dict:
    """The summary as plain data, for --json and for checking the web page against."""
    reports, skipped = loaded.reports, loaded.skipped
    return {
        "reports": len(reports),
        "streams": [{
            "source": s.source, "domain": s.domain, "selector": s.selector,
            "first": str(s.first), "last": str(s.last), "missing_days": s.missing,
            "columns": [k for _, k in s.columns],
            "days": [{"day": str(d), "version": r.version, "segments": [
                {"segment": list(seg.path), "classification": seg.classification, "engagement": seg.engagement}
                for seg in r.segments]} for d, r in sorted(s.by_day.items())],
            "changes": s.changes, "notes": s.notes,
        } for s in streams(reports)],
        "skipped": [{"where": w, "reason": r} for w, r in skipped],
        "warnings": [{"warning": w, "times": n, "first": where, "detail": d} for w, n, where, d in grouped(loaded.warnings)],
        "copies": loaded.copies,
    }
