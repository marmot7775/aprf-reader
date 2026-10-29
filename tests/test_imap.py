import gzip
import imaplib
import json
import ssl
from datetime import date

import pytest

from aprf_reader import cli, imap
from aprf_reader.imap import ImapError, criteria, fetch
from aprf_reader.load import load

from test_reader import report, report_email


class FakeImap:
    """Answers the calls fetch() makes the way imaplib returns a server's replies."""

    def __init__(self, messages, password="right", folders=("INBOX", "APRF")):
        self.messages = messages  # {uid: raw bytes}
        self.password, self.folders = password, folders
        self.calls = []

    def login(self, user, password):
        if password != self.password:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")
        return "OK", [b"logged in"]

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        if folder.strip('"') not in self.folders:
            return "NO", [b"[NONEXISTENT] Unknown Mailbox"]
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command, *args):
        self.calls.append((command, *args))
        if command == "SEARCH":
            return "OK", [b" ".join(str(u).encode() for u in self.messages)]
        out = []
        for u in args[0].split(","):
            body = self.messages[int(u)]
            out += [(f"{u} (UID {u} RFC822.SIZE {len(body)} BODY[] {{{len(body)}}}".encode(), body), b")"]
        return "OK", out

    def logout(self):
        self.calls.append(("logout",))
        return "BYE", [b""]


def mails(n=3):
    out = {}
    for d in range(n):
        out[100 + d] = report_email(d, gzip.compress(json.dumps(report(d)).encode()), "gzip", f"{d}.json.gz").as_bytes()
    return out


def test_fetch_is_read_only_and_batched(monkeypatch):
    monkeypatch.setattr(imap, "BATCH", 2)
    fake = FakeImap(mails(5))
    got = list(fetch("imap.example.com", "u", "right", "APRF", conn=fake))
    assert [label for label, _ in got] == [f"imap://imap.example.com/APRF uid {u}" for u in range(100, 105)]
    assert ("select", '"APRF"', True) in fake.calls  # EXAMINE, not SELECT
    fetches = [c for c in fake.calls if c[0] == "FETCH"]
    assert len(fetches) == 3 and all("BODY.PEEK[]" in c[2] for c in fetches)  # PEEK leaves \\Seen alone
    assert fake.calls[-1] == ("logout",)


def test_criteria():
    assert criteria() == ["SUBJECT", '"APRF:"']
    assert criteria(date(2026, 10, 1)) == ["SUBJECT", '"APRF:"', "SINCE", "01-Oct-2026"]
    assert criteria(every=True) == ["ALL"]


def test_login_and_folder_errors_are_plain():
    with pytest.raises(ImapError, match="login failed: .*Invalid credentials"):
        list(fetch("h", "u", "wrong", conn=FakeImap({})))
    with pytest.raises(ImapError, match="opening folder Nope failed: .*Unknown Mailbox"):
        list(fetch("h", "u", "right", "Nope", conn=FakeImap({})))


def test_fetched_mail_read_like_an_mbox():
    msgs = list(mails(2).items())
    note = b"Subject: APRF: hello\r\n\r\nno attachment\r\n"
    loaded = load([], None, [(f"m{u}", b) for u, b in msgs] + [("m9", note), ("m10", note)], "imap://h/APRF")
    assert sorted(r.day.day for r in loaded.reports) == [1, 2]
    assert loaded.skipped == [("imap://h/APRF", "2 messages with no report attached")]


def test_cli_imap(monkeypatch, capsys):
    fake = FakeImap(mails(2))
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host, port, ssl_context, timeout: fake)
    monkeypatch.setenv("APRF_IMAP_PASSWORD", "right")
    assert cli.main(["--imap", "imap.example.com", "--user", "u", "--folder", "APRF", "--json"]) == 0
    out, err = capsys.readouterr()
    assert json.loads(out)["reports"] == 2
    assert "read 2 messages from APRF on imap.example.com" in err


def test_cli_imap_failure_and_usage(monkeypatch, capsys):
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host, port, ssl_context, timeout: FakeImap({}))
    monkeypatch.setenv("APRF_IMAP_PASSWORD", "wrong")
    assert cli.main(["--imap", "h", "--user", "u"]) == 2
    assert "login failed" in capsys.readouterr().err
    for argv in ([], ["--imap", "h"], ["--imap", "h:x", "--user", "u"]):
        with pytest.raises(SystemExit):
            cli.main(argv)


def test_certificate_is_checked(monkeypatch):
    seen = {}

    def connect(host, port, ssl_context, timeout):
        seen["ctx"] = ssl_context
        return FakeImap({})

    monkeypatch.setattr(imaplib, "IMAP4_SSL", connect)
    list(fetch("h", "u", "right"))
    assert seen["ctx"].verify_mode == ssl.CERT_REQUIRED and seen["ctx"].check_hostname
