"""Regressions for the 2026-11 bug hunt, issues #32 to #42. Each test failed before its fix.

Where the web page reads the same input, the test also runs the page's reader and
requires the same output, as test_web_parity.py does."""

import json
import re
import shutil
from pathlib import Path

import pytest

from aprf_reader.cli import main
from aprf_reader.load import load
from aprf_reader.render import CAVEAT, to_markdown, to_terminal
from aprf_reader.summary import NOTHING_COMPARED, ONE_REPORT, streams, to_dict
from test_web_parity import same, web

ROOT = Path(__file__).parent.parent
DAY = 86400
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
ALL_CLEAR = "No big day-to-day moves"


def rep(day: int, body=None, **header) -> dict:
    start = 1790812800 + day * DAY  # 2026-10-01
    h = {"version": 1, "source": "P", "dkim_domain": "example.com", "dkim_selector": "s1",
         "report_start": start, "report_end": start + DAY - 1, "sdi_used": "N/A", **header}
    return {"header": h, "body": [{"classification": {"inbox": 1000}}] if body is None else body}


def put(path: Path, obj) -> Path:
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj))
    return path


def check(paths: list[Path]) -> None:
    """The page reads these files as the command line does, when node is here to run it."""
    if NODE:
        same(paths)


# ---- #32: no all-clear when nothing was compared ----

@pytest.mark.parametrize("reports,why", [
    ([rep(0)], ONE_REPORT),
    ([rep(0, [{"classification": {"forwarded": 500}}]), rep(1, [{"classification": {"forwarded": 500}}])],
     NOTHING_COMPARED),
    # inbox only on day one, unwanted only on day two: no count is on both
    ([rep(0, [{"classification": {"inbox": 500}}]), rep(1, [{"classification": {"unwanted": 500}}])],
     NOTHING_COMPARED),
])
def test_nothing_compared_is_not_an_all_clear(tmp_path, reports, why):
    p = put(tmp_path / "r.json", reports)
    loaded = load([p])
    (s,) = streams(loaded.reports)
    assert s.changes == [] and s.not_compared == why
    out = to_terminal(loaded, color=False)
    assert ALL_CLEAR not in out and why in out
    assert to_dict(loaded)["streams"][0]["not_compared"] == why
    check([p])


def test_compared_and_steady_is_an_all_clear(tmp_path):
    p = put(tmp_path / "r.json", [rep(0), rep(1)])
    loaded = load([p])
    (s,) = streams(loaded.reports)
    assert s.not_compared is None and ALL_CLEAR in to_terminal(loaded, color=False)
    assert to_dict(loaded)["streams"][0]["not_compared"] is None
    check([p])


# ---- #33: a file that comes back after a changed copy is the later copy ----

def test_a_report_read_again_after_a_changed_copy_replaces_it(tmp_path):
    (tmp_path / "a").mkdir()
    one = put(tmp_path / "a/1.json", rep(0, [{"classification": {"inbox": 1000}}]))
    put(tmp_path / "a/2.json", rep(0, [{"classification": {"inbox": 500}}]))
    (tmp_path / "a/3.json").write_bytes(one.read_bytes())
    loaded = load([tmp_path / "a"])
    (kept,) = loaded.reports
    assert kept.segments[0].classification["inbox"] == 1000
    assert loaded.copies == 0
    replaced = [(d, where) for w, d, where in loaded.warnings if w.startswith("the same day came twice")]
    assert replaced == [(f"{tmp_path}/a/1.json replaced, with different counts", f"{tmp_path}/a/2.json"),
                        (f"{tmp_path}/a/2.json replaced, with different counts", f"{tmp_path}/a/3.json")]
    check(sorted((tmp_path / "a").iterdir()))


def test_a_true_repeat_is_still_a_copy(tmp_path):
    # A file holding the same day twice, read twice: the second read changes nothing
    a = put(tmp_path / "a.json", [rep(0, [{"classification": {"inbox": 1000}}]), rep(0, [{"classification": {"inbox": 500}}])])
    (tmp_path / "b.json").write_bytes(a.read_bytes())
    loaded = load([a, tmp_path / "b.json"])
    assert loaded.copies == 1 and loaded.reports[0].segments[0].classification["inbox"] == 500
    check([a, tmp_path / "b.json"])


# ---- #35: counts are often bucketed, and the change rule follows the stream's step ----

def test_a_collapse_in_ten_wide_buckets_is_a_change(tmp_path):
    p = put(tmp_path / "r.json", [rep(0, [{"classification": {"inbox": 150}}]), rep(1, [{"classification": {"inbox": 10}}])])
    loaded = load([p])
    (s,) = streams(loaded.reports)
    assert s.step == 10 and s.min_move == 20
    assert s.changes == ["inbox fell from 150 on 2026-10-01 to 10 on 2026-10-02."]
    assert ALL_CLEAR not in to_terminal(loaded, color=False)
    check([p])


def test_one_step_of_100_is_still_rounding(tmp_path):
    # Comcast-shaped counts keep the old rule: 300 to 100 is two steps, 200 to 100 one
    p = put(tmp_path / "r.json", [rep(0, [{"classification": {"inbox": 200}}]), rep(1, [{"classification": {"inbox": 100}}]),
                                  rep(2, [{"classification": {"inbox": 300}}]), rep(3, [{"classification": {"inbox": 100}}])])
    (s,) = streams(load([p]).reports)
    assert s.min_move == 200
    assert s.changes == ["inbox fell from 300 on 2026-10-03 to 100 on 2026-10-04."]
    check([p])


def test_caveat_does_not_say_every_count_is_a_bucket(tmp_path):
    assert not CAVEAT.startswith("Counts are bucket upper bounds")
    assert "may round counts into buckets" in CAVEAT and "steps of 100" in CAVEAT
    p = put(tmp_path / "r.json", rep(0))
    assert CAVEAT in to_markdown(load([p]))
    check([p])
    # The page's chart tip and label say the count, not "up to" it, which assumes a bucket
    assert "up to " not in (ROOT / "web/ui/app.js").read_text()


# ---- #36: sdi_used with a separator the draft forbids ----

SDI_WARNING = "sdi_used is not N/A, N/F, or the sdi value as published in DNS (section 5.2.1)"


@pytest.mark.parametrize("sdi", ["X-Foo,;", "X-Foo,=", "X-Foo,,"])
def test_forbidden_sdi_separator_warns(tmp_path, sdi):
    p = put(tmp_path / "r.json", rep(0, sdi_used=sdi))
    assert [d for w, d, _ in load([p]).warnings if w == SDI_WARNING] == [json.dumps(sdi)]
    check([p])


@pytest.mark.parametrize("sdi", ["X-Foo,:", "X;Foo,|"])
def test_allowed_sdi_separator_does_not_warn(tmp_path, sdi):
    p = put(tmp_path / "r.json", rep(0, sdi_used=sdi))
    assert not [w for w, _, _ in load([p]).warnings if w == SDI_WARNING]
    check([p])


# ---- #37: a number too long to read is a skip, not a crash ----

HUGE = "9" * 5000


@pytest.mark.parametrize("text,reason", [
    (json.dumps(rep(0)).replace('"inbox": 1000', '"inbox": ' + HUGE),
     "body[0].classification.inbox is not a count (section 6): null"),
    (json.dumps(rep(0)).replace('"report_start": 1790812800', '"report_start": ' + HUGE),
     "header.report_start is not an epoch time (section 5.2.1)"),
    (json.dumps(rep(0)).replace('"inbox": 1000', '"inbox": -' + HUGE),
     "body[0].classification.inbox is not a count (section 6): null"),
])
def test_huge_number_is_skipped_and_the_rest_read(tmp_path, capsys, text, reason):
    big = put(tmp_path / "big.json", text)
    ok = put(tmp_path / "ok.json", rep(1))
    loaded = load([big, ok])
    assert len(loaded.reports) == 1 and loaded.skipped == [(str(big), reason)]
    assert main([str(tmp_path), "--markdown"]) == 0
    assert "2026-10-02" in capsys.readouterr().out
    check([big, ok])


# ---- #38: a lone surrogate escape is read as U+FFFD ----

def test_lone_surrogate_is_read_as_replacement_character(tmp_path, capsys):
    text = json.dumps(rep(0, [{"segment": ["a\ud800b"], "classification": {"inbox": 1000}}]))
    assert "\\ud800" in text
    p = put(tmp_path / "sur.json", text)
    out = tmp_path / "out.html"
    assert main([str(p), "--markdown", "--html", str(out)]) == 0
    assert "| a�b |" in capsys.readouterr().out
    assert "a�b" in out.read_text(encoding="utf-8")
    assert main([str(p)]) == 0
    check([p])


@pytest.mark.parametrize("raw,read", [
    ('"x\\ud83d\\ude00y"', "x\U0001f600y"),  # a pair is one character
    ('"x\\\\ud800y"', "x\\ud800y"),  # an escaped backslash, then plain text
    ('"\\udc00\\ud800"', "��"),  # low then high is two lone halves
])
def test_surrogate_pairs_and_escaped_backslashes_are_left_alone(tmp_path, raw, read):
    text = json.dumps(rep(0, [{"segment": ["PLACEHOLDER"], "classification": {"inbox": 1000}}])).replace('"PLACEHOLDER"', raw)
    p = put(tmp_path / "r.json", text)
    (r,) = load([p]).reports
    assert r.segments[0].path == (read,)
    check([p])


# ---- #39: -0 is 0 ----

@pytest.mark.parametrize("zero", ["-0", "-0.0"])
def test_negative_zero_reads_as_zero(tmp_path, zero):
    text = json.dumps(rep(0, [{"classification": {"inbox": 1000, "unwanted": 7}}])).replace('"unwanted": 7', '"unwanted": ' + zero)
    p = put(tmp_path / "r.json", text)
    md = to_markdown(load([p]))
    assert "| 2026-10-01 | all mail | 1,000 | 0 |" in md
    if NODE:
        assert "| 2026-10-01 | all mail | 1,000 | 0 |" in web("--md", [p])
        assert "<td>1,000<td>0\n" in web("--html", [p])
    check([p])


# ---- #40: an output path that cannot be written ----

@pytest.mark.parametrize("flag", ["--html", "--md"])
def test_output_to_a_missing_folder_is_one_line_and_exit_2(tmp_path, capsys, flag):
    p = put(tmp_path / "r.json", rep(0))
    target = tmp_path / "nonexistent" / "x.out"
    assert main([str(p), flag, str(target)]) == 2
    err = capsys.readouterr().err
    assert err.strip() == f"aprf-reader: cannot write {target}: no such folder {target.parent}"


def test_output_to_a_folder_is_refused(tmp_path, capsys):
    p = put(tmp_path / "r.json", rep(0))
    assert main([str(p), "--html", str(tmp_path)]) == 2
    assert f"cannot write {tmp_path}: is a folder" in capsys.readouterr().err


def test_a_failed_html_write_still_prints_the_summary(tmp_path, capsys, monkeypatch):
    p = put(tmp_path / "r.json", rep(0))
    real = Path.write_text

    def full(self, *a, **k):
        if self.name == "x.html":
            raise OSError(28, "No space left on device")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", full)
    assert main([str(p), "--markdown", "--html", str(tmp_path / "x.html")]) == 2
    out, err = capsys.readouterr()
    assert "# APRF placement summary" in out
    assert err.strip() == f"aprf-reader: cannot write {tmp_path / 'x.html'}: No space left on device"


# ---- #41: chart values by tap and screen reader ----

def test_chart_values_by_tap_and_screen_reader():
    src = (ROOT / "web/ui/app.js").read_text()
    chart = src[src.index("function chart("):src.index("// ---- results ----")]
    # A tap shows the day: a press, not only a move, and a touch leaving does not hide it
    assert re.search(r"addEventListener\('pointerdown', \(e\) => show\(nearest\(e\.clientX\), true\)\)", chart)
    assert "e.pointerType !== 'touch'" in chart
    # Keys and taps write the day into a live region inside the chart's figure
    assert re.search(r"const said = h\('p', \{ class: 'sr-only', 'aria-live': 'polite' \}\)", chart)
    assert "said.textContent" in chart and "show(next[0], true)" in chart
    assert re.search(r"svg, tip, said\)", chart)


# ---- #42: a stream is headed with its domain as written ----

def test_international_domain_heading_as_written(tmp_path, capsys):
    p = put(tmp_path / "idn.json", [rep(0, dkim_domain="bücher.example", dkim_selector="Sel1"),
                                    rep(1, dkim_domain="xn--bcher-kva.example", dkim_selector="sel1")])
    loaded = load([p])
    (s,) = streams(loaded.reports)
    assert (s.domain, s.domain_key, s.selector) == ("bücher.example", "xn--bcher-kva.example", "Sel1")
    assert "## P: bücher.example (xn--bcher-kva.example), selector Sel1" in to_markdown(loaded)
    assert "bücher.example (xn--bcher-kva.example)  selector Sel1" in to_terminal(loaded, color=False)
    d = to_dict(loaded)["streams"][0]
    assert (d["domain"], d["domain_key"], d["selector"]) == ("bücher.example", "xn--bcher-kva.example", "Sel1")
    check([p])


def test_ascii_domain_heading_has_no_second_form(tmp_path):
    p = put(tmp_path / "r.json", rep(0, dkim_domain="Example.COM"))
    assert "## P: Example.COM, selector s1" in to_markdown(load([p]))
    check([p])
