"""Regressions for the defects filed in issues #11 to #22. Each test failed before its fix.

Where the web page reads the same input, the test also runs the page's reader and
requires the same output, as test_web_parity.py does."""

import gzip
import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from aprf_reader.cli import main
from aprf_reader.load import load
from aprf_reader.render import to_html, to_markdown, to_terminal
from aprf_reader.report import ReportError, parse, parse_items
from aprf_reader.summary import streams, to_dict
from test_web_parity import same, web

ROOT = Path(__file__).parent.parent
DAY = 86400
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def rep(day: int, body=None, **header) -> dict:
    start = 1790812800 + day * DAY  # 2026-10-01
    h = {"version": 1, "source": "P", "dkim_domain": "example.com", "dkim_selector": "s1",
         "report_start": start, "report_end": start + DAY - 1, "sdi_used": "N/A", **header}
    h = {k: v for k, v in h.items() if v is not ...}  # ... removes a field
    return {"header": h, "body": [{"classification": {"inbox": 100}}] if body is None else body}


def put(path: Path, obj) -> Path:
    path.write_text(json.dumps(obj))
    return path


def read(path: Path):
    return load([path])


FULL = {"classification": {"inbox": 5000, "unwanted": 100}, "engagement": {"positive": 300, "negative": 0}}


# ---- #11: a count the report does not carry is missing, not zero ----

def _engagement_only_day(tmp_path) -> Path:
    return put(tmp_path / "r.json", [rep(0, [FULL]), rep(1, [{"engagement": FULL["engagement"]}]), rep(2, [FULL])])


def test_absent_count_is_missing_not_zero(tmp_path):
    p = _engagement_only_day(tmp_path)
    (s,) = streams(read(p).reports)
    inbox = [i for i, (_, k) in enumerate(s.columns) if k == "inbox"][0]
    assert s.trend(inbox) == [(0, 5000), (2, 5000)]
    assert s.changes == []
    out = to_terminal(read(p), color=False)
    assert "5,000 every day" in out and "0 to 5,000" not in out
    same([p])


def test_absent_count_does_not_hide_a_real_move(tmp_path):
    # inbox 5000, missing, 100: the move is measured between the days that carry the count
    p = put(tmp_path / "r.json", [rep(0, [FULL]), rep(1, [{"engagement": FULL["engagement"]}]),
                                  rep(2, [{"classification": {"inbox": 100, "unwanted": 100}}])])
    (s,) = streams(read(p).reports)
    assert s.changes == []  # nothing is computed across the missing day
    same([p])


# ---- #13: a report with no segments is a visible day with no counts ----

def test_empty_body_is_a_row_and_raises_no_alarm(tmp_path):
    p = put(tmp_path / "r.json", [rep(0, [{"classification": {"inbox": 5000, "unwanted": 100}}]), rep(1, []),
                                  rep(2, [{"classification": {"inbox": 5000, "unwanted": 400}}])])
    loaded = read(p)
    (s,) = streams(loaded.reports)
    assert [(str(r.day), r.segment) for r in s.rows] == [("2026-10-01", "all mail"), ("2026-10-02", "no counts"),
                                                          ("2026-10-03", "all mail")]
    assert s.changes == []
    assert "| 2026-10-02 | no counts |  |  |" in to_markdown(loaded)
    same([p])


# ---- #12: provider text never reaches the terminal with control characters ----

def test_terminal_view_escapes_control_characters(tmp_path):
    bad = "ok\x1b[3A\r\x1b[2K  unwanted      0  0 every day\x9b\x07\x7f"
    p = put(tmp_path / "r.json", [rep(0, extra_info=bad, source="S\x1b[31m"), rep(1)])
    loaded = read(p)
    out = to_terminal(loaded, color=False)
    assert not re.search(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]", out)
    assert "ok\\x1b[3A\\x0d\\x1b[2K" in out and "S\\x1b[31m" in out


def test_skip_lines_on_stderr_escape_control_characters(tmp_path, capsys):
    bad = tmp_path / "evil\x1b[2J.json"
    bad.write_text("{oops")
    put(tmp_path / "ok.json", rep(0))
    assert main([str(tmp_path)]) == 0
    err = capsys.readouterr().err
    assert "\x1b" not in err and "evil\\x1b[2J.json" in err


# ---- #14: the saved page says nothing it cannot back ----

def test_saved_page_does_not_claim_nothing_left(tmp_path):
    p = put(tmp_path / "r.json", rep(0))
    assert "Nothing left" not in to_html(read(p))
    if NODE:
        assert "Nothing left" not in web("--html", [p])
        same([p])


@needs_node
def test_usage_note_carries_no_domains():
    """CLAUDE.md: the usage note never carries report content, domains, or addresses."""
    js = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const m = /function note\(loaded, sample\) \{[\s\S]*?\n  \}\n/.exec(src);
if (!m) throw new Error('note() not found');
const posted = [];
const fetch = (url, opts) => { posted.push(opts.body); return Promise.resolve(); };
const document = { referrer: '' }, location = { hostname: 'aprf.dns-audit.com' };
const APRF = { streams: () => [{ domain: 'client-example.com' }] };
// eval is intended: it runs only note(), cut from this repository's own app.js, with stubbed globals
eval(m[0] + ';note({ reports: [1], warnings: [], skipped: [], copies: 0 }, false)');
process.stdout.write(posted.join('\n'));
"""
    out = subprocess.run([NODE, "-e", js, str(ROOT / "web/ui/app.js")], check=True, capture_output=True).stdout.decode()
    assert out and "client-example.com" not in out and "domains" not in out


# ---- #15, #19, #20: header values mean the same thing in both readers ----

def test_numerically_equal_versions_are_one_version(tmp_path):
    p = put(tmp_path / "r.json", [rep(0, version=1.0), rep(1, version=1)])
    (s,) = streams(read(p).reports)
    assert not [n for n in s.notes if "version changed" in n]
    assert s.by_day[date(2026, 10, 1)].version == "1"
    same([p])


def test_non_scalar_required_text_is_skipped_with_a_reason(tmp_path):
    for field, bad in (("source", ["X"]), ("version", True), ("source", {"a": 1}), ("version", [[1]])):
        with pytest.raises(ReportError, match=rf"header\.{field} .*section 5\.2\.1"):
            parse(json.dumps(rep(0, **{field: bad})).encode())


def test_non_scalar_optional_text_warns_and_is_ignored(tmp_path):
    p = put(tmp_path / "r.json", [rep(0, extra_info=[], contact_info={"a": 1}), rep(1)])
    loaded = read(p)
    assert len(loaded.reports) == 2 and not loaded.skipped
    assert all(r.extra_info == "" and r.contact_info == "" for r in loaded.reports)
    assert [w for w, _, _ in loaded.warnings].count("header.extra_info is not text or a number (section 5.2.1); ignored") == 1
    (s,) = streams(loaded.reports)
    assert not [n for n in s.notes if n.startswith("Provider note")]
    same([p])


def test_missing_source_or_version_is_read_the_same_in_both(tmp_path):
    # Issue #20 asks whether these should be skipped (the README table) or read (the code and
    # tests/fixtures/stress_100.json). That is open, so this only holds the two readers together.
    p = put(tmp_path / "r.json", [rep(0), rep(1, source=..., version=...), rep(2, version=None)])
    assert len(read(p).reports) == 3
    same([p])


def _nested(depth: int, field: str = "extra_info") -> str:
    return ('{"header":{"version":1,"source":"P","dkim_domain":"example.com","dkim_selector":"s1",'
            '"report_start":1790812800,"report_end":1790899199,"sdi_used":"N/A","' + field + '":'
            + "[" * depth + "]" * depth + '},"body":[{"classification":{"inbox":100}}]}')


@pytest.mark.parametrize("depth", [200, 5000])
def test_deeply_nested_header_value_costs_nothing(tmp_path, depth):
    (tmp_path / "n.json").write_text(_nested(depth))
    put(tmp_path / "ok.json", rep(1))
    loaded = load([tmp_path / "n.json", tmp_path / "ok.json"])
    assert len(loaded.reports) == 1 and loaded.skipped[0][1] == "JSON nested too deeply"
    same([tmp_path / "n.json", tmp_path / "ok.json"])


def test_one_deep_report_does_not_cost_the_rest_of_the_file(tmp_path):
    ok = json.dumps(rep(1))
    (tmp_path / "n.json").write_text("[" + _nested(200) + "," + ok + "]")
    loaded = read(tmp_path / "n.json")
    assert [str(r.day) for r in loaded.reports] == ["2026-10-02"]
    assert loaded.skipped == [(f"{tmp_path}/n.json report 1", "JSON nested too deeply")]
    same([tmp_path / "n.json"])


def test_deeply_nested_count_is_a_skip_not_a_crash(tmp_path):
    text = ('{"header":{"version":1,"source":"P","dkim_domain":"example.com","dkim_selector":"s1",'
            '"report_start":1790812800,"report_end":1790899199,"sdi_used":"N/A"},"body":[{"classification":{"inbox":'
            + "[" * 100000 + "]" * 100000 + '}}]}')
    (tmp_path / "n.json").write_text(text)
    put(tmp_path / "ok.json", rep(1))
    loaded = load([tmp_path / "n.json", tmp_path / "ok.json"])
    assert len(loaded.reports) == 1 and len(loaded.skipped) == 1
    same([tmp_path / "n.json", tmp_path / "ok.json"])


# ---- #16: one global name order for files ----

def test_files_are_read_in_one_name_order(tmp_path):
    a = put(tmp_path / "a.json", rep(0, [{"classification": {"inbox": 5000}}]))
    b = put(tmp_path / "b.json", rep(0, [{"classification": {"inbox": 900}}]))
    # Name order is a.json then b.json, and the later copy replaces the earlier (section 8.2)
    (kept,) = load([b, a]).reports
    assert kept.segments[0].classification["inbox"] == 900
    assert to_dict(load([b, a])) == to_dict(load([a, b]))
    if NODE:
        assert json.loads(web("--json", [b, a])) == to_dict(load([b, a]))


def test_folder_order_is_the_full_path_order(tmp_path):
    new, old = tmp_path / "APRF/APRF/20261001.json", tmp_path / "APRF/APRF-old/20261001.json"
    for p, n in ((new, 5000), (old, 900)):
        p.parent.mkdir(parents=True)
        put(p, rep(0, [{"classification": {"inbox": n}}]))
    loaded = load([tmp_path / "APRF"])
    # "APRF-old/..." sorts before "APRF/..." as strings ("-" is before "/"), so APRF/APRF is the later copy
    assert loaded.reports[0].segments[0].classification["inbox"] == 5000
    assert loaded.warnings[0][1].startswith(f"{old} replaced")
    if NODE:
        assert json.loads(web("--json", [new, old])) == to_dict(loaded)


# ---- #17: count names such as constructor and __proto__ are plain names ----

@needs_node
def test_count_names_that_are_object_properties(tmp_path):
    body = [{"segment": "a", "classification": {"inbox": 100, "constructor": 5, "__proto__": 7, "toString": 1}},
            {"segment": "b", "classification": {"inbox": 100}}]
    p = tmp_path / "r.json"
    p.write_text(json.dumps(rep(0, body)))
    md = to_markdown(read(p))
    assert "function" not in md and "__proto__" in md
    same([p])
    assert "function" not in web("--md", [p])


# ---- #18: reports are UTF-8, as RFC 8259 requires ----

@pytest.mark.parametrize("enc", ["utf-16", "utf-16-le", "utf-16-be", "utf-32"])
def test_non_utf8_json_is_refused(tmp_path, enc):
    (tmp_path / "t.json").write_bytes(json.dumps(rep(0)).encode(enc))
    loaded = read(tmp_path / "t.json")
    assert not loaded.reports and loaded.skipped[0][1].startswith("not valid JSON, which section 5 requires")
    same([tmp_path / "t.json"])


def test_utf8_with_a_byte_order_mark_is_read(tmp_path):
    (tmp_path / "t.json").write_bytes(b"\xef\xbb\xbf" + json.dumps(rep(0)).encode())
    assert len(read(tmp_path / "t.json").reports) == 1
    same([tmp_path / "t.json"])


# ---- #21: a carriage return or other line break cannot split a Markdown table row ----

def test_line_breaks_in_provider_text_stay_in_one_row(tmp_path):
    seg = "news\r| 2026-10-01 | all mail | 999,999 | 0 |"
    body = [{"segment": seg + " x y\r\nz", "classification": {"inbox": 100, "unwanted": 5000}}]
    p = put(tmp_path / "r.json", rep(0, body, sdi_used="X-Seg,:", extra_info="a\rb"))
    md = to_markdown(read(p))
    assert not re.search(r"[\r  ]", md)
    row = [line for line in md.split("\n") if line.startswith("| 2026-10-01 |")]
    assert len(row) == 1 and row[0].endswith("| 100 | 5,000 |")
    same([p])


# ---- #22: RFC 2231 continuations in a file name ----

def _eml(disposition: str) -> bytes:
    raw = json.dumps(rep(0, source="P")).encode()
    import base64
    return (b"From: a@example.net\r\nSubject: APRF: 20261001_example.com_s1_P\r\nMIME-Version: 1.0\r\n"
            b"Content-Type: multipart/report; boundary=B\r\n\r\n--B\r\nContent-Type: text/plain\r\n\r\nhi\r\n--B\r\n"
            b"Content-Type: application/json\r\nContent-Transfer-Encoding: base64\r\n"
            + disposition.encode() + b"\r\n\r\n" + base64.encodebytes(raw).replace(b"\n", b"\r\n") + b"\r\n--B--\r\n")


@pytest.mark.parametrize("disposition", [
    'Content-Disposition: attachment;\r\n filename*0="20261001_example.com_";\r\n filename*1="s1_P.json"',
    'Content-Disposition: attachment;\r\n filename*1="s1_P.json";\r\n filename*0="20261001_example.com_"',
    "Content-Disposition: attachment;\r\n filename*0*=utf-8''20261001_exam;\r\n filename*1*=ple.com%5F;\r\n filename*2=\"s1_P.json\"",
])
def test_continued_file_name_is_joined(tmp_path, disposition):
    p = tmp_path / "m.eml"
    p.write_bytes(_eml(disposition))
    loaded = read(p)
    assert len(loaded.reports) == 1
    assert not [w for w, _, _ in loaded.warnings if "attachment name" in w]
    same([p])
