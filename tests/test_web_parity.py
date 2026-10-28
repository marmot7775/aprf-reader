"""The web page (web/src/aprf.js) must read files exactly as the command line does."""

import gzip
import json
import mailbox
import shutil
import subprocess
from email.message import EmailMessage
from pathlib import Path

import pytest

from aprf_reader import __version__
from aprf_reader.load import load
from aprf_reader.render import to_html, to_markdown
from aprf_reader.summary import to_dict

ROOT = Path(__file__).parent.parent
FIX = Path(__file__).parent / "fixtures"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def web(mode: str, paths: list[Path]) -> str:
    return subprocess.run([NODE, str(ROOT / "web/test/run.js"), mode, *map(str, paths)], check=True,
                          capture_output=True, text=True, env={"APRF_VERSION": __version__}).stdout


def same(paths: list[Path]) -> None:
    loaded = load(paths)
    assert json.loads(web("--json", paths)) == to_dict(loaded.reports, loaded.skipped)
    assert web("--md", paths) == to_markdown(loaded)
    assert web("--html", paths) == to_html(loaded)


def report(day: int, selector: str = "s1", inbox: int = 100) -> dict:
    start = 1790812800 + day * 86400
    return {"header": {"version": 1, "source": "Comcast/Xfinity", "dkim_domain": "Example.com",
                       "dkim_selector": selector, "report_start": start, "report_end": start + 86399,
                       "sdi_used": " - N/A - Beta - Not in use"},
            "body": [{"classification": {"inbox": inbox, "unwanted": 0}, "engagement": {"positive": 0}}]}


def email(payload: bytes, subtype: str, filename: str | None, cte: str | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "aprf-noreply@example.net"
    msg["Subject"] = "APRF: test"
    msg.set_content("Report attached.")
    # cte=None would mean raw binary, not the base64 default
    msg.add_attachment(payload, maintype="application", subtype=subtype, filename=filename, **({"cte": cte} if cte else {}))
    return msg


@pytest.mark.parametrize("name", ["draft_sample_1.json", "draft_sample_2.json", "comcast_beta.json", "sample_six_weeks.json",
                                  "stress_100.json"])
def test_fixtures(name):
    same([FIX / name])


def test_all_fixtures_together():
    same(sorted(FIX.glob("*.json")))


def test_files_emails_and_mailboxes(tmp_path):
    def put(name: str, data: bytes) -> Path:
        (tmp_path / name).write_bytes(data)
        return tmp_path / name

    raw = lambda day, **kw: json.dumps(report(day, **kw)).encode()
    paths = [
        put("a.json", raw(0)),
        put("b.json.gz", gzip.compress(raw(1))),
        put("c.eml", email(gzip.compress(raw(2)), "gzip", "20261003_example.com_s1_Comcast.json.gz").as_bytes()),
        put("d.eml", email(raw(3), "octet-stream", None).as_bytes()),
        put("e.eml", email(raw(4), "json", "r.json", cte="quoted-printable").as_bytes()),
        put("copy.json", raw(0)),
        put("resend.json", raw(0, inbox=900)),
        put("note.eml", email(b"hello", "pdf", "invoice.pdf").as_bytes()),
    ]
    # A report email forwarded as an attachment
    outer = EmailMessage()
    outer["Subject"] = "Fwd: APRF"
    outer.set_content("See attached.")
    outer.add_attachment(email(raw(5, inbox=3000), "json", "f.json"))
    paths.append(put("fwd.eml", outer.as_bytes()))
    box = mailbox.mbox(tmp_path / "Reports.mbox")
    for day in (6, 7, 9):
        box.add(email(gzip.compress(raw(day, selector="s2")), "gzip", f"{day}.json.gz"))
    box.add(email(raw(8, selector="s2"), "json", "same.json"))
    plain = EmailMessage()
    plain.set_content("From the team: not a report")
    box.add(plain)
    box.close()
    paths.append(tmp_path / "Reports.mbox")
    same(paths)


def test_hostile_text_escaped_the_same(tmp_path):
    obj = report(0)
    hostile = "<script>alert('x')</script>|a\\b \"q\" & é\nnew"
    obj["header"].update(source=hostile, extra_info=hostile, sdi_used=hostile)
    obj["body"][0]["segment"] = [hostile, "two"]
    obj["body"][0]["classification"][hostile] = 7
    (tmp_path / "h.json").write_text(json.dumps(obj))
    same([tmp_path / "h.json"])
