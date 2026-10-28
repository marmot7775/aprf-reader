import gzip
import json
import mailbox
from datetime import date
from email.message import EmailMessage
from pathlib import Path

import pytest

from aprf_reader.cli import main
from aprf_reader.load import load
from aprf_reader.render import to_html, to_markdown
from aprf_reader.summary import streams
from aprf_reader.report import ReportError, parse

FIX = Path(__file__).parent / "fixtures"
DAY = 86400


def report(day: int, inbox: int = 100, source: str = "Comcast", **header) -> dict:
    start = 1790812800 + day * DAY  # 2026-10-01 00:00 UTC
    h = {"version": "1", "source": source, "dkim_domain": "example.com", "dkim_selector": "s1",
         "report_start": start, "report_end": start + DAY - 1, "sdi_used": "N/F", **header}
    return {"header": h, "body": [{"classification": {"inbox": inbox, "unwanted": 10},
                                   "engagement": {"positive": 5, "negative": 1}}]}


def write(path: Path, obj) -> Path:
    path.write_text(json.dumps(obj))
    return path


def test_draft_samples_parse():
    (r1,) = parse((FIX / "draft_sample_1.json").read_bytes())
    assert r1.dkim_selector == "selector1" and r1.version == "42"
    assert [s.path for s in r1.segments] == [("Seg1", "Seg2", "Seg3"), ("Seg1", "Seg2", "Other"), ("Seg1", "Other")]
    assert r1.segments[0].classification == {"inbox": 10000, "unwanted": 500}
    assert r1.day == date(2024, 2, 29)
    (r2,) = parse((FIX / "draft_sample_2.json").read_bytes())
    assert r2.segments[0].path == () and r2.sdi_used == "N/F"


def test_comcast_beta_report(tmp_path, capsys):
    # Shape of a real Comcast beta report as published by Spam Resource, July 2026; domain replaced
    (r,) = parse((FIX / "comcast_beta.json").read_bytes())
    assert r.version == "1" and r.sdi_used == "N/A - Beta - Not in use"
    assert main([str(FIX / "comcast_beta.json")]) == 0
    out = capsys.readouterr().out
    assert "2026-07-13: 1 report" in out and " to 2026-07-13" not in out
    assert "SDI used" not in out


def test_bare_object_and_string_segment():
    obj = report(0)
    obj["body"][0]["segment"] = "CustomerA"
    (r,) = parse(json.dumps(obj).encode())
    assert r.segments[0].path == ("CustomerA",)


@pytest.mark.parametrize("mutate, reason", [
    (lambda o: o.pop("header"), "no header"),
    (lambda o: o["header"].pop("dkim_domain"), "dkim_domain"),
    (lambda o: o["header"].update(report_end="yesterday"), "report_end"),
    (lambda o: o["body"][0]["classification"].update(inbox=-1), "not a count"),
    (lambda o: o["body"][0]["engagement"].update(positive=True), "not a count"),
])
def test_bad_reports_rejected(mutate, reason):
    obj = report(0)
    mutate(obj)
    with pytest.raises(ReportError, match=reason):
        parse(json.dumps(obj).encode())


def test_load_gz_eml_folder_and_skips(tmp_path):
    write(tmp_path / "a.json", report(0))
    (tmp_path / "b.json.gz").write_bytes(gzip.compress(json.dumps(report(1)).encode()))
    msg = EmailMessage()
    msg["Subject"] = "APRF: 20261003_example.com_s1_Comcast"
    msg.set_content("report attached")
    msg.add_attachment(gzip.compress(json.dumps(report(2)).encode()), maintype="application",
                       subtype="gzip", filename="20261003_example.com_s1_Comcast.json.gz")
    (tmp_path / "c.eml").write_bytes(msg.as_bytes())
    write(tmp_path / "copy.json", report(0))  # same bytes as a.json
    write(tmp_path / "resend.json", report(0, inbox=999))  # same day, different counts
    (tmp_path / "broken.json").write_text("{not json")
    (tmp_path / "notes.txt").write_text("ignored: not a report suffix")

    loaded = load([tmp_path])
    assert sorted(r.day.day for r in loaded.reports) == [1, 2, 3]
    reasons = dict((Path(w.split(" ")[0]).name, r) for w, r in loaded.skipped)
    assert set(reasons) == {"copy.json", "resend.json", "broken.json"}
    assert "duplicate" in reasons["copy.json"] and "same day" in reasons["resend.json"]


def report_email(day: int, payload: bytes, subtype: str, filename: str | None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "aprf-noreply@alerts.xfinity.com"
    msg["Subject"] = f"APRF: day {day}"
    msg.set_content("report attached")
    msg.add_attachment(payload, maintype="application", subtype=subtype, filename=filename)
    return msg


def test_attachment_found_by_content(tmp_path):
    # A provider may label the file octet-stream or give it no useful name
    raw = json.dumps(report(0)).encode()
    (tmp_path / "plain.eml").write_bytes(report_email(0, raw, "octet-stream", "20261001_example.com_s1_Comcast").as_bytes())
    gz = gzip.compress(json.dumps(report(1)).encode())
    (tmp_path / "gz.eml").write_bytes(report_email(1, gz, "octet-stream", None).as_bytes())
    loaded = load([tmp_path])
    assert sorted(r.day.day for r in loaded.reports) == [1, 2] and not loaded.skipped


def test_mbox_export(tmp_path):
    (tmp_path / "Reports.mbox").mkdir()  # Apple Mail's export layout
    box = mailbox.mbox(tmp_path / "Reports.mbox" / "mbox")
    box.add(report_email(0, gzip.compress(json.dumps(report(0)).encode()), "gzip", "a.json.gz"))
    box.add(report_email(1, json.dumps(report(1)).encode(), "json", "b.json"))
    note = EmailMessage()
    note["Subject"] = "not a report"
    note.set_content("hello")
    box.add(note)
    box.add(note)
    box.add(report_email(2, b"\x1f\x8b broken", "gzip", "c.json.gz"))
    box.add(report_email(3, b"not gzip at all", "gzip", "d.json.gz"))
    box.add(report_email(4, json.dumps(report(4)).encode(), "json", "e.json"))
    box.close()
    loaded = load([tmp_path])
    assert sorted(r.day.day for r in loaded.reports) == [1, 2, 5]  # read on past bad messages
    reasons = [r for _, r in loaded.skipped]
    assert len(reasons) == 3 and "2 messages with no report attached" in reasons


def test_hostile_files_skipped_not_crashed(tmp_path):
    (tmp_path / "deep.json").write_text("[" * 100_000 + "]" * 100_000)
    write(tmp_path / "far.json", report(0, report_end=10**20))
    good = gzip.compress(json.dumps(report(0)).encode())
    (tmp_path / "bad-deflate.json.gz").write_bytes(good[:10] + b"\x07" * (len(good) - 10))
    (tmp_path / "truncated.json.gz").write_bytes(good[:-12])
    loaded = load([tmp_path])
    assert not loaded.reports and len(loaded.skipped) == 4
    reasons = " ".join(r for _, r in loaded.skipped)
    assert "nested too deeply" in reasons and "out of range" in reasons


def test_gzip_bomb_skipped(tmp_path):
    (tmp_path / "big.json.gz").write_bytes(gzip.compress(b" " * (11 * 1024 * 1024)))
    loaded = load([tmp_path])
    assert not loaded.reports and "10 MB" in loaded.skipped[0][1]


def test_stream_gaps_versions_and_columns(tmp_path):
    write(tmp_path / "1.json", report(0))
    write(tmp_path / "2.json", report(3, version="2", extra_info="positive now includes replies"))
    write(tmp_path / "3.json", report(1, source="Other MBP"))
    ss = streams(load([tmp_path]).reports)
    assert [s.source for s in ss] == ["Comcast", "Other MBP"]
    s = ss[0]
    assert s.missing == 2
    assert [k for _, k in s.columns] == ["inbox", "unwanted", "positive", "negative"]
    gaps = [(r.day, r.until) for r in s.rows if r.segment is None]
    assert gaps == [(date(2026, 10, 2), date(2026, 10, 3))]  # one row per run, not per day
    assert [x for x, _ in s.trend(0)] == [0, 3]
    notes = " ".join(s.notes)
    assert "SDI" not in notes  # N/F means none
    assert "changed from 1 to 2 on 2026-10-04" in notes and "replies" in notes


def test_outputs_escape_report_text(tmp_path):
    hostile = "<script>alert(1)</script>|x"
    obj = report(0, source=hostile)
    obj["body"][0]["segment"] = [hostile]
    write(tmp_path / "r.json", obj)
    loaded = load([tmp_path])
    page = to_html(loaded)
    assert "<script>" not in page and "&lt;script&gt;" in page
    md = to_markdown(loaded)
    assert "</script>\\|x" in md and "</script>|x" not in md


def test_cli(tmp_path, capsys):
    out = tmp_path / "out.html"
    assert main([str(FIX), "--html", str(out)]) == 0
    stdout = capsys.readouterr().out
    assert "Receiver MBP, Inc.: example.com, selector selector1" in stdout
    assert "| 2024-02-29 | Seg1 / Seg2 / Seg3 | 10,000 | 500 | 300 | 50 | 200 |" in stdout
    assert out.read_text().startswith("<!doctype html>")
    (tmp_path / "empty").mkdir()
    assert main([str(tmp_path / "empty")]) == 1
