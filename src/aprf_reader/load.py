"""Find report files, open them, and drop duplicates."""

from __future__ import annotations

import gzip
import hashlib
import io
import mailbox
import re
import zipfile
import zlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from email import policy
from email.message import Message
from email.parser import BytesParser
from pathlib import Path

from .report import Report, ReportError, parse_items

SUFFIXES = (".json", ".json.gz", ".eml", ".mbox", ".zip")
# A zip of a year of daily reports for a few selectors is a few thousand files
MAX_ZIP_ENTRIES = 20_000
# A daily report is a few KB; this stops a stray huge file or gzip bomb
MAX_BYTES = 10 * 1024 * 1024


@dataclass
class Loaded:
    reports: list[Report] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (where, reason)
    # (warning, detail, where): read, but departs from the draft or meets a case it leaves open
    warnings: list[tuple[str, str, str]] = field(default_factory=list)
    # Files whose report bytes were already read, often the same report as .json and .eml
    copies: int = 0


@dataclass(frozen=True)
class Mail:
    """How a report arrived by email, for the section 8 checks."""
    name: str  # attachment file name as sent
    ctype: str  # attachment Content-Type
    gz: bool
    subject: str
    msg_ctype: str  # Content-Type of the message that carries it

DUPLICATE_DAY = ("the same day came twice from one provider; the first copy read was kept. "
                 "The draft leaves this open (a note in section 8.1)")


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


def _leaves(m: Message, owner: Message) -> Iterator[tuple[Message, Message]]:
    """Each non-multipart part with the email it belongs to; an attached email owns its own parts."""
    if m.get_content_type() == "message/rfc822":
        for inner in m.get_payload():
            yield from _leaves(inner, inner)
    elif m.is_multipart():
        for p in m.get_payload():
            yield from _leaves(p, owner)
    else:
        yield m, owner


def _attachments(msg: Message, label: str) -> Iterator[tuple[str, bytes, Mail]]:
    """Yield (label, report JSON bytes, how it arrived) for each report attached to one email."""
    for part, owner in _leaves(msg, msg):
        name = part.get_filename() or ""
        fname = name.lower()
        ctype = part.get_content_type()
        data = part.get_payload(decode=True) or b""
        where = f"{label} ({fname or ctype})"
        if len(data) > MAX_BYTES:
            raise ReportError(f"{where} is larger than 10 MB")
        mail = lambda gz: Mail(name, ctype, gz, " ".join(str(owner.get("subject", "")).split()), owner.get_content_type())
        if fname.endswith(".gz") or ctype in ("application/gzip", "application/x-gzip") or data[:2] == b"\x1f\x8b":
            yield where, _gunzip(data), mail(True)
        # By name or type, or an attachment of unhelpful type that is a JSON array or object
        elif fname.endswith(".json") or ctype == "application/json" or (
                part.get_content_disposition() == "attachment" and data.lstrip()[:1] in (b"[", b"{")):
            yield where, data, mail(False)


def _payloads(label: str, data: bytes) -> Iterator[tuple[str, bytes, Mail | None]]:
    """Yield (label, report JSON bytes, how it arrived) for each report in one file's bytes."""
    if len(data) > MAX_BYTES:
        raise ReportError("larger than 10 MB")
    if label.lower().endswith(".eml"):
        found = list(_attachments(BytesParser(policy=policy.default).parsebytes(data), label))
        if not found:
            raise ReportError("no report attachment, which section 8 calls for")
        yield from found
    elif data[:2] == b"\x1f\x8b":
        yield label, _gunzip(data), None
    else:
        yield label, data, None


def _zip(path: Path, out: Loaded) -> Iterator[tuple[str, bytes]]:
    """Yield (label, bytes) for each report file in a zip, in name order; a bad member is skipped, not fatal."""
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ReportError("not a readable zip file") from None
    with z:
        # __MACOSX holds Finder metadata copies named like the real files
        members = sorted((i for i in z.infolist() if not i.is_dir() and not i.filename.startswith("__MACOSX/")
                          and i.filename.lower().endswith(SUFFIXES)), key=lambda i: i.filename)
        if len(members) > MAX_ZIP_ENTRIES:
            raise ReportError(f"more than {MAX_ZIP_ENTRIES:,} report files in one zip")
        for i in members:
            label = f"{path}/{i.filename}"
            name = i.filename.lower()
            if name.endswith((".zip", ".mbox")):
                out.skipped.append((label, "a zip or mailbox inside a zip is not read; unzip it first"))
            elif i.flag_bits & 1:
                out.skipped.append((label, "encrypted in the zip"))
            elif i.file_size > MAX_BYTES:
                out.skipped.append((label, "larger than 10 MB"))
            else:
                try:
                    with z.open(i) as f:
                        data = f.read(MAX_BYTES + 1)
                except (zipfile.BadZipFile, NotImplementedError, zlib.error, EOFError, OSError):
                    out.skipped.append((label, "could not be unpacked from the zip"))
                    continue
                yield label, data


def _mbox(path: Path, out: Loaded) -> Iterator[tuple[str, bytes, Mail | None]]:
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


def _differs(kept: Report, dropped: Report) -> str:
    if kept.version != dropped.version:
        return f", version {kept.version or 'none'} kept, version {dropped.version or 'none'} dropped"
    return ", with different counts" if kept.segments != dropped.segments else ", same counts"


def _name(r: Report) -> str:
    # Section 8: the ending date, then domain, selector and source with spaces removed
    return f"{r.day:%Y%m%d}_{r.dkim_domain}_{r.dkim_selector}_{r.source.replace(' ', '')}"


# Characters a file name cannot hold on common systems; "Comcast/Xfinity" and a "*" roll-up
# selector both carry one, and section 8 only says to remove spaces
UNSAFE = '/\\:*?"<>|'
NAME_UNSAFE = ("attachment name stands in for a character a file name cannot hold, such as / or *; "
               "section 8 does not say how to write those")
SUBJECT_UNSAFE = ("subject stands in for / or * as a file name must, though section 8 gives the source "
                  "and selector as they are")


def _loose(expected: str, actual: str) -> bool:
    """Equal, ignoring case, if each file-name-unsafe character in expected may be any character."""
    pattern = "".join("." if c in UNSAFE else re.escape(c) for c in expected.lower())
    return re.fullmatch(pattern, actual.lower()) is not None


def _mail_warnings(mail: Mail, items: list) -> list[tuple[str, str]]:
    out = []
    if mail.msg_ctype != "multipart/report":
        out.append(("message Content-Type is not multipart/report (section 8)", mail.msg_ctype))
    want = "application/gzip" if mail.gz else "application/json"
    if mail.ctype != want:
        out.append(("attachment Content-Type is not application/json, or application/gzip when "
                    "compressed (sections 8 and 8.1)", mail.ctype))
    if len(items) > 1:
        out.append(("one attachment holds several reports; the section 8 name and subject fit one", f"{len(items)} reports"))
    elif isinstance(items[0], Report):
        base = _name(items[0])
        file = base + (".json.gz" if mail.gz else ".json")
        if mail.name.lower() == file.lower():
            pass
        elif _loose(file, mail.name):
            out.append((NAME_UNSAFE, f"{mail.name}; section 8 gives {file}"))
        else:
            out.append(("attachment name does not follow section 8: <yyyymmdd>_<DKIM domain>_<DKIM selector>_<source>.json",
                        f"{mail.name or '(none)'}; expected {file}"))
        if mail.subject.lower() == f"APRF: {base}".lower():
            pass
        elif _loose(f"APRF: {base}", mail.subject):
            out.append((SUBJECT_UNSAFE, f"{mail.subject}; section 8 gives APRF: {base}"))
        else:
            out.append(("subject does not follow section 8: APRF: <yyyymmdd>_<DKIM domain>_<DKIM selector>_<source>",
                        f"{mail.subject or '(none)'}; expected APRF: {base}"))
    return out


def pasted_name(data: bytes) -> str:
    """A name for pasted text, so it is read like a file: a report's JSON, or a whole
    email such as Gmail's Show original, attachment and all."""
    head = data.lstrip()[:1]
    if head in (b"[", b"{"):
        return "pasted.json"
    return "pasted.json.gz" if data[:2] == b"\x1f\x8b" else "pasted.eml"


def load(paths: Iterable[Path], pasted: bytes | None = None) -> Loaded:
    out = Loaded()
    seen_bytes: set[str] = set()
    seen_keys: dict[tuple, tuple[str, Report]] = {}

    parsed: dict[str, list | ReportError] = {}

    def add(where: str, data: bytes, mail: Mail | None) -> None:
        digest = hashlib.sha256(data).hexdigest()
        copy = digest in parsed
        if not copy:
            try:
                parsed[digest] = parse_items(data)
            except ReportError as e:
                parsed[digest] = e
        items = parsed[digest]
        # An email is checked even when its report was already read from another file
        if mail is not None and not isinstance(items, ReportError):
            out.warnings += [(w, d, where) for w, d in _mail_warnings(mail, items)]
        if copy:
            out.copies += 1
            return
        if isinstance(items, ReportError):
            out.skipped.append((where, str(items)))
            return
        for i, r in enumerate(items, 1):
            label = f"{where} report {i}" if len(items) > 1 else where
            if isinstance(r, ReportError):
                out.skipped.append((label, str(r)))
                continue
            out.warnings += [(w, d, label) for w, d in r.warnings]
            # The draft leaves open whether a resend replaces the first (an open note in
            # section 8.1), and its version is a free string with no order; keep the first
            if r.key in seen_keys:
                first_label, first = seen_keys[r.key]
                out.warnings.append((DUPLICATE_DAY, f"{first_label} kept{_differs(first, r)}", label))
                continue
            seen_keys[r.key] = (label, r)
            out.reports.append(r)

    for path in _files(paths):
        try:
            if path.name.lower().endswith(".zip"):
                for label, data in _zip(path, out):
                    try:
                        for where, d, mail in list(_payloads(label, data)):
                            add(where, d, mail)
                    except (OSError, EOFError, zlib.error, ReportError) as e:
                        out.skipped.append((label, str(e)))
                continue
            # An mbox can be large, so it is read one message at a time
            payloads = _mbox(path, out) if _is_mbox(path) else list(_payloads(str(path), path.read_bytes()))
            for where, data, mail in payloads:
                add(where, data, mail)
        except (OSError, EOFError, zlib.error, ReportError) as e:
            out.skipped.append((str(path), str(e)))
    if pasted is not None:
        name = pasted_name(pasted)
        try:
            for where, data, mail in list(_payloads(name, pasted)):
                add(where, data, mail)
        except (OSError, EOFError, zlib.error, ReportError) as e:
            out.skipped.append((name, str(e)))
    return out
