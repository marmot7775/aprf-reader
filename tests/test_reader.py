import gzip
import json
import mailbox
import re
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
    assert [Path(w).name for w, _ in loaded.skipped] == ["broken.json"]
    assert loaded.copies == 1  # copy.json has a.json's bytes
    warned = {(w.split(";")[0].split(".")[0], Path(where.split(" ")[0]).name) for w, _, where in loaded.warnings}
    assert ("the same day came twice from one provider", "resend.json") in warned
    # The test email is multipart/mixed; its name and subject follow section 8
    assert {w for w, _, where in loaded.warnings if "c.eml" in where} == {"message Content-Type is not multipart/report (section 8)"}


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


def test_changes_flag_big_moves_only():
    (s1, s2) = streams(parse((FIX / "sample_six_weeks.json").read_bytes()))
    assert s1.selector == "news" and s1.changes == [
        "unwanted rose from 300 on 2026-09-24 to 2,500 on 2026-09-25.",
        "negative rose from 100 on 2026-09-24 to 400 on 2026-09-25.",
        "inbox fell from 2,200 on 2026-09-25 to 600 on 2026-09-26.",
    ]
    assert s2.changes == []  # steady stream, nothing flagged
    # 10 to 200 is many times higher but under two buckets of movement
    (s,) = streams([parse(json.dumps(report(0, inbox=100)).encode())[0],
                    parse(json.dumps(report(1, inbox=100) | {"body": [{"classification": {"unwanted": 200}}]}).encode())[0]])
    assert not any("unwanted" in c for c in s.changes)


def test_json_output(capsys):
    assert main([str(FIX / "sample_six_weeks.json"), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["reports"] == 80 and [s["selector"] for s in doc["streams"]] == ["news", "receipts"]
    news = doc["streams"][0]
    assert news["missing_days"] == 2 and news["columns"] == ["inbox", "unwanted", "positive", "negative"]
    assert news["days"][0]["segments"][0]["segment"] == [] and len(news["changes"]) == 3


def test_terminal_view():
    from aprf_reader.render import to_terminal
    loaded = load([FIX / "sample_six_weeks.json", FIX / "comcast_beta.json"])
    out = to_terminal(loaded, color=False, width=100)
    assert "example.com  selector news" in out and "2 days with no report" in out
    assert "! unwanted rose from 300 on Sep 24 to 2,500 on Sep 25." in out
    assert "✓ No big day-to-day moves" in out and "one day" in out
    assert "\033[" not in out and "\033[" in to_terminal(loaded, color=True)
    assert max(len(line) for line in out.splitlines()) <= 100
    inbox = next(line for line in out.splitlines() if line.startswith("  inbox"))
    assert re.search("[▁-█] +[▁-█]", inbox)  # the two days with no report show as a break


def test_cli_terminal_when_interactive(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setenv("NO_COLOR", "1")
    assert main([str(FIX / "sample_six_weeks.json")]) == 0
    out = capsys.readouterr().out
    assert out.startswith("APRF placement summary") and "\033[" not in out
    assert main([str(FIX / "sample_six_weeks.json"), "--markdown"]) == 0
    assert capsys.readouterr().out.startswith("# APRF placement summary")


def test_stress_set_one_bad_report_does_not_cost_the_file():
    loaded = load([FIX / "stress_100.json"])
    assert len(loaded.reports) == 86
    reasons = {w.rsplit(" ", 1)[-1]: r for w, r in loaded.skipped}
    # s18 spans two days and s19 is shifted an hour: draft section 5.1 requires one UTC day
    assert "section 5.1" in reasons["77"] and "section 5.1" in reasons["78"]
    # Same day from the same provider: keep the first, warn, and say how the copy differs
    dup = {where.rsplit(" ", 1)[-1]: d for w, d, where in loaded.warnings if w.startswith("the same day came twice")}
    assert dup["64"] == f"{FIX}/stress_100.json report 63 kept, version 1 kept, version 2 dropped"
    assert dup["69"].endswith("report 68 kept, with different counts")  # bücher.example
    assert "report 3 kept" in dup["70"]  # DNS-AUDIT.COM / SELECTOR1 is dns-audit.com / selector1
    assert not {"64", "69", "70"} & set(reasons)
    warned = {w for w, _, _ in loaded.warnings}
    assert "sdi_used is not N/A, N/F, or the sdi value as published in DNS (section 5.2.1)" in warned
    assert any(w.startswith('classification key "spam"') for w in warned)
    assert not any('"promotional"' in w or '"forwarded"' in w for w in warned)  # section 6 names them
    assert reasons["73"] == 'body[0].engagement.positive is not a count (section 7): "100"'
    assert reasons["79"] == "header.report_end is before report_start (section 5.1)"
    assert reasons["81"].startswith("header.report_start looks like milliseconds")
    ss = {(s.source, s.domain, s.selector): s for s in streams(loaded.reports)}
    assert ("Big MBP Co", "dns-audit.com", "s11") in ss  # another provider, same day: its own stream
    obj = report(0)
    obj["body"][0]["classification"]["unwanted"] = 100.0
    assert parse(json.dumps(obj).encode())[0].segments[0].classification["unwanted"] == 100
    for sel in ("sdi0", "sdi1", "sdi2", "sdi3", "sdi4"):  # n/a, "N/A ", "", null, N/F;
        assert not ss[("Comcast/Xfinity", "dns-audit.com", sel)].notes, sel
    blank = ss[("Comcast/Xfinity", "dns-audit.com", "s13")]
    assert "(blank)" in [r.segment for r in blank.rows]
    assert not ss[("Comcast/Xfinity", "dns-audit.com", "s8")].columns
    assert "These reports carry no counts." in to_markdown(loaded)
