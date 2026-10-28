"""Find report files, open them, and drop duplicates."""

from __future__ import annotations

import gzip
import hashlib
import io
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from pathlib import Path

from .report import Report, ReportError, parse

SUFFIXES = (".json", ".json.gz", ".eml")
# A daily report is a few KB; this stops a stray huge file or gzip bomb
MAX_BYTES = 10 * 1024 * 1024


@dataclass
class Loaded:
    reports: list[Report] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (where, reason)


def _files(paths: Iterable[Path]) -> Iterator[Path]:
    for p in paths:
        if p.is_dir():
            yield from sorted(f for f in p.rglob("*") if f.is_file() and f.name.lower().endswith(SUFFIXES))
        else:
            yield p


def _gunzip(data: bytes) -> bytes:
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as g:
        out = g.read(MAX_BYTES + 1)
    if len(out) > MAX_BYTES:
        raise ReportError("larger than 10 MB unpacked")
    return out


def _payloads(path: Path) -> Iterator[tuple[str, bytes]]:
    """Yield (label, report JSON bytes) for each report in a file."""
    data = path.read_bytes()
    if len(data) > MAX_BYTES:
        raise ReportError("larger than 10 MB")
    name = path.name.lower()
    if name.endswith(".eml"):
        msg = BytesParser(policy=policy.default).parsebytes(data)
        found = False
        for part in msg.walk():
            fname = (part.get_filename() or "").lower()
            ctype = part.get_content_type()
            if fname.endswith(".json.gz") or ctype == "application/gzip":
                found = True
                yield f"{path} ({fname or ctype})", _gunzip(part.get_payload(decode=True) or b"")
            elif fname.endswith(".json") or ctype == "application/json":
                found = True
                yield f"{path} ({fname or ctype})", part.get_payload(decode=True) or b""
        if not found:
            raise ReportError("no .json or .json.gz attachment")
    elif data[:2] == b"\x1f\x8b":
        yield str(path), _gunzip(data)
    else:
        yield str(path), data


def load(paths: Iterable[Path]) -> Loaded:
    out = Loaded()
    seen_bytes: set[str] = set()
    seen_keys: dict[tuple, str] = {}
    for path in _files(paths):
        try:
            payloads = list(_payloads(path))
        except (OSError, EOFError, gzip.BadGzipFile, ReportError) as e:
            out.skipped.append((str(path), str(e)))
            continue
        for where, data in payloads:
            digest = hashlib.sha256(data).hexdigest()
            if digest in seen_bytes:
                out.skipped.append((where, "duplicate of a file already read"))
                continue
            seen_bytes.add(digest)
            try:
                reports = parse(data)
            except ReportError as e:
                out.skipped.append((where, str(e)))
                continue
            for r in reports:
                # The draft does not yet say whether a resend replaces the first; keep the first
                if r.key in seen_keys:
                    out.skipped.append((where, f"same day already reported in {seen_keys[r.key]}; kept that one"))
                    continue
                seen_keys[r.key] = where
                out.reports.append(r)
    return out
