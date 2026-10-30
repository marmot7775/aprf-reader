"""Read report emails straight from a mailbox over IMAP, without changing anything there."""

from __future__ import annotations

import base64
import imaplib
import re
import ssl
from collections.abc import Iterator
from datetime import date

from .load import MAX_BYTES

# Comcast's subjects start "APRF:"; IMAP SUBJECT matches a substring, without case
SUBJECT = "APRF:"
# Messages fetched per round trip
BATCH = 50
# A few years of daily reports for a few selectors; more is a wrong folder
MAX_MESSAGES = 20_000


class ImapError(Exception):
    pass


def _check(typ: str, data: list, what: str) -> list:
    if typ != "OK":
        detail = b" ".join(d for d in data if isinstance(d, bytes)).decode(errors="replace")
        raise ImapError(f"{what} failed: {detail or typ}")
    return data


def _text(e: Exception) -> str:
    # imaplib puts the server's reply bytes in the exception as they came
    a = e.args[0] if e.args else ""
    return a.decode(errors="replace") if isinstance(a, bytes) else str(e)


def utf7(name: str) -> str:
    """A folder name in IMAP's modified UTF-7 (RFC 3501 5.1.3), as Exchange and others name
    folders such as "Gelöschte Elemente"; plain ASCII is unchanged."""
    out, run = [], []

    def flush():
        if run:
            b = base64.b64encode("".join(run).encode("utf-16-be")).decode().rstrip("=").replace("/", ",")
            out.append(f"&{b}-")
            run.clear()

    for ch in name:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            run.append(ch)
    flush()
    return "".join(out)


def _login(m, user: str, password: str) -> None:
    if user.isascii() and password.isascii():
        _check(*m.login(user, password), "login")
    else:
        # LOGIN only carries ASCII; SASL PLAIN carries UTF-8 (RFC 4616)
        if "AUTH=PLAIN" not in getattr(m, "capabilities", ("AUTH=PLAIN",)):
            raise ImapError("login failed: the user name or password has non-ASCII characters, "
                            "and this server does not offer AUTH=PLAIN, which is needed to send them")
        creds = b"\0" + user.encode() + b"\0" + password.encode()
        _check(*m.authenticate("PLAIN", lambda _: creds), "login")


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def criteria(since: date | None = None, every: bool = False) -> list[str]:
    out = [] if every else ["SUBJECT", _quote(SUBJECT)]
    if since:
        out += ["SINCE", since.strftime("%d-%b-%Y")]
    return out or ["ALL"]


def _messages(m, host: str, folder: str, since: date | None, every: bool) -> Iterator[tuple[str, bytes | None]]:
    (ids,) = _check(*m.uid("SEARCH", *criteria(since, every)), "search")
    uids = (ids or b"").split()
    if len(uids) > MAX_MESSAGES:
        raise ImapError(f"{len(uids):,} messages match, more than {MAX_MESSAGES:,}; "
                        "pick the folder the reports are filed in, or use --since")
    for i in range(0, len(uids), BATCH):
        chunk = b",".join(uids[i:i + BATCH]).decode()
        # Sizes first, so a huge message is never downloaded
        sizes = {}
        for item in _check(*m.uid("FETCH", chunk, "(UID RFC822.SIZE)"), "fetch"):
            line = item[0] if isinstance(item, tuple) else item
            u, n = re.search(rb"UID (\d+)", line or b""), re.search(rb"RFC822\.SIZE (\d+)", line or b"")
            if u and n:
                sizes[u.group(1)] = int(n.group(1))
        big = [u for u in uids[i:i + BATCH] if sizes.get(u, 0) > MAX_BYTES]
        for u in big:
            yield f"imap://{host}/{folder} uid {u.decode()}", None
        small = [u for u in uids[i:i + BATCH] if u not in big]
        if not small:
            continue
        data = _check(*m.uid("FETCH", b",".join(small).decode(), "(UID BODY.PEEK[])"), "fetch")
        for item in data:
            if not isinstance(item, tuple):
                continue  # the closing ")" of each response
            head, body = item
            # Most servers put UID before the body; one that puts it after still has a sequence number
            uid = re.search(rb"UID (\d+)", head)
            which = f"uid {uid.group(1).decode()}" if uid else f"message {head.split(b' ', 1)[0].decode(errors='replace')}"
            yield f"imap://{host}/{folder} {which}", body


def fetch(host: str, user: str, password: str, folder: str = "INBOX", port: int = 993,
          since: date | None = None, every: bool = False, conn=None) -> Iterator[tuple[str, bytes | None]]:
    """Yield (label, raw message bytes) for each matching message in one folder; the
    bytes are None for a message over MAX_BYTES, which is not downloaded.

    The folder is opened read-only and bodies are fetched with BODY.PEEK, so no
    message is marked read, moved or deleted."""
    # imaplib's own default context checks neither the certificate nor the host name
    m = conn or imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=60)
    try:
        try:
            _login(m, user, password)
        except imaplib.IMAP4.error as e:
            raise ImapError(f"login failed: {_text(e)}") from None
        try:
            _check(*m.select(_quote(utf7(folder)), readonly=True), f"opening folder {folder}")
        except imaplib.IMAP4.error as e:
            raise ImapError(f"opening folder {folder} failed: {_text(e)}") from None
        try:
            yield from _messages(m, host, folder, since, every)
        except imaplib.IMAP4.error as e:  # includes abort: the server hung up mid-run
            raise ImapError(f"connection lost: {_text(e)}") from None
    finally:
        try:
            m.logout()
        except (imaplib.IMAP4.error, OSError):
            pass
