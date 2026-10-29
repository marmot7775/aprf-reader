"""Read report emails straight from a mailbox over IMAP, without changing anything there."""

from __future__ import annotations

import imaplib
import re
from collections.abc import Iterator
from datetime import date

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


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def criteria(since: date | None = None, every: bool = False) -> list[str]:
    out = [] if every else ["SUBJECT", _quote(SUBJECT)]
    if since:
        out += ["SINCE", since.strftime("%d-%b-%Y")]
    return out or ["ALL"]


def fetch(host: str, user: str, password: str, folder: str = "INBOX", port: int = 993,
          since: date | None = None, every: bool = False, conn=None) -> Iterator[tuple[str, bytes]]:
    """Yield (label, raw message bytes) for each matching message in one folder.

    The folder is opened read-only and bodies are fetched with BODY.PEEK, so no
    message is marked read, moved or deleted."""
    m = conn or imaplib.IMAP4_SSL(host, port, timeout=60)
    try:
        try:
            _check(*m.login(user, password), "login")
        except imaplib.IMAP4.error as e:
            raise ImapError(f"login failed: {e}") from None
        try:
            _check(*m.select(_quote(folder), readonly=True), f"opening folder {folder}")
        except imaplib.IMAP4.error as e:
            raise ImapError(f"opening folder {folder} failed: {e}") from None
        (ids,) = _check(*m.uid("SEARCH", *criteria(since, every)), "search")
        uids = (ids or b"").split()
        if len(uids) > MAX_MESSAGES:
            raise ImapError(f"{len(uids):,} messages match, more than {MAX_MESSAGES:,}; "
                            "pick the folder the reports are filed in, or use --since")
        for i in range(0, len(uids), BATCH):
            chunk = b",".join(uids[i:i + BATCH]).decode()
            data = _check(*m.uid("FETCH", chunk, "(UID RFC822.SIZE BODY.PEEK[])"), "fetch")
            for item in data:
                if not isinstance(item, tuple):
                    continue  # the closing ")" of each response
                head, body = item
                # Most servers put UID before the body; one that puts it after still has a sequence number
                uid = re.search(rb"UID (\d+)", head)
                which = f"uid {uid.group(1).decode()}" if uid else f"message {head.split(b' ', 1)[0].decode(errors='replace')}"
                label = f"imap://{host}/{folder} {which}"
                yield label, body
    finally:
        try:
            m.logout()
        except (imaplib.IMAP4.error, OSError):
            pass
