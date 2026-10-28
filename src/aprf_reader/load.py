"""Find report files, open them, and drop duplicates."""

from __future__ import annotations

import gzip
import hashlib
import io
import mailbox
import zlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from email import policy
from email.message import Message
from email.parser import BytesParser
from pathlib import Path

from .report import Report, ReportError, parse_items

SUFFIXES = (".json", ".json.gz", ".eml", ".mbox")
# A daily report is a few KB; this stops a stray huge file or gzip bomb
MAX_BYTES = 10 * 1024 * 1024


@dataclass
class Loaded:
    reports: list[Report] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (where, reason)


def _files(paths: Iterable[Path]) -> Iterator[Path]:
    for p in paths:
        if p.is_dir():
            yield from sorted(f for f in p.rglob("*") if f.is_file() and (f.name.lower().endswith(SUFFIXES) or _is_mbox(f)))
        else:
            yield p


def _is_mbox(path: Path) -> bool:
    # Apple Mail exports a folder "Name.mbox" holding a file named just "mbox"
    return path.name.lower() == "mbox" or path.name.lower().endswith(".mbox")


def _gunzip(data: bytes) -> bytes:
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as g:
        out = g.read(MAX_BYTES + 1)
    if len(out) > MAX_BYTES:
        raise ReportError("larger than 10 MB unpacked")
    return out


def _attachments(msg: Message, label: str) -> Iterator[tuple[str, bytes]]:
    """Yield (label, report JSON bytes) for each report attached to one email."""
    for part in msg.walk():
        if part.is_multipart():
            continue
        fname = (part.get_filename() or "").lower()
        ctype = part.get_content_type()
        data = part.get_payload(decode=True) or b""
        where = f"{label} ({fname or ctype})"
        if len(data) > MAX_BYTES:
            raise ReportError(f"{where} is larger than 10 MB")
        if fname.endswith(".gz") or ctype in ("application/gzip", "application/x-gzip") or data[:2] == b"\x1f\x8b":
            yield where, _gunzip(data)
        # By name or type, or an attachment of unhelpful type that is a JSON array or object
        elif fname.endswith(".json") or ctype == "application/json" or (
                part.get_content_disposition() == "attachment" and data.lstrip()[:1] in (b"[", b"{")):
            yield where, data


def _payloads(path: Path) -> Iterator[tuple[str, bytes]]:
    """Yield (label, report JSON bytes) for each report in a file."""
    data = path.read_bytes()
    if len(data) > MAX_BYTES:
        raise ReportError("larger than 10 MB")
    if path.name.lower().endswith(".eml"):
        found = list(_attachments(BytesParser(policy=policy.default).parsebytes(data), str(path)))
        if not found:
            raise ReportError("no report attachment")
        yield from found
    elif data[:2] == b"\x1f\x8b":
        yield str(path), _gunzip(data)
    else:
        yield str(path), data


def _mbox(path: Path, out: Loaded) -> Iterator[tuple[str, bytes]]:
    """Yield reports from a mailbox export; one skip line for all mail with none attached."""
    box = mailbox.mbox(path, factory=lambda f: BytesParser(policy=policy.default).parse(f), create=False)
    without = 0
    try:
        for i, msg in enumerate(box, 1):
            label = f"{path} message {i}"
            try:
                found = list(_attachments(msg, label))
            # OSError covers gzip.BadGzipFile; one bad message must not end the mailbox
            except (OSError, EOFError, zlib.error, ReportError) as e:
                out.skipped.append((label, str(e)))
                continue
            without += not found
            yield from found
    finally:
        box.close()
    if without:
        out.skipped.append((str(path), f"{without} message{'s' if without != 1 else ''} with no report attached"))


def load(paths: Iterable[Path]) -> Loaded:
    out = Loaded()
    seen_bytes: set[str] = set()
    seen_keys: dict[tuple, str] = {}

    def add(where: str, data: bytes) -> None:
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen_bytes:
            out.skipped.append((where, "duplicate of a file already read"))
            return
        seen_bytes.add(digest)
        try:
            items = parse_items(data)
        except ReportError as e:
            out.skipped.append((where, str(e)))
            return
        for i, r in enumerate(items, 1):
            label = f"{where} report {i}" if len(items) > 1 else where
            if isinstance(r, ReportError):
                out.skipped.append((label, str(r)))
                continue
            # The draft does not yet say whether a resend replaces the first; keep the first
            if r.key in seen_keys:
                out.skipped.append((label, f"same day already reported in {seen_keys[r.key]}; kept that one"))
                continue
            seen_keys[r.key] = label
            out.reports.append(r)

    for path in _files(paths):
        try:
            # An mbox can be large, so it is read one message at a time
            payloads = _mbox(path, out) if _is_mbox(path) else list(_payloads(path))
            for where, data in payloads:
                add(where, data)
        except (OSError, EOFError, zlib.error, ReportError) as e:
            out.skipped.append((str(path), str(e)))
    return out
