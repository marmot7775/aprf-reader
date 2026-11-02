"""
Skip reasons, as the codes the usage note sends.

The page shows a person why a file was not read, in a sentence that names the
file and can quote the parse error. Neither can leave the browser, so the
note sends a code from a closed set instead. A code still has to say the one
thing worth knowing: whether the reader correctly declined a file (a nested
zip, an encrypted member, something over the size cap) or could not parse a
report it should have read. The second is how a bug in the reader shows up in
the log at all.

The mapping is by phrase, so it breaks quietly if a reason is reworded. The
guard below reads the reason literals straight out of aprf.js and fails if any
of them no longer classifies, which is the failure that would otherwise be
invisible until the log had been lying for a month.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"
APRF_JS = WEB / "src" / "aprf.js"

_DRIVER = (
    'let s = "";'
    'process.stdin.on("data", (d) => { s += d; })'
    '.on("end", () => {'
    '  const A = require(process.env.APRF_JS);'
    '  console.log(JSON.stringify(JSON.parse(s).map((r) => A.skipCode(r))));'
    "});"
)


def codes(reasons):
    """skipCode() for each reason, as the page itself would compute it."""
    out = subprocess.run(
        ["node", "-e", _DRIVER],
        input=json.dumps(reasons),
        capture_output=True,
        text=True,
        env={"APRF_JS": str(APRF_JS), "PATH": "/usr/bin:/bin:/usr/local/bin"},
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


KNOWN = [
    ("a zip or mailbox inside a zip is not read; unzip it first", "nested_archive"),
    ("encrypted in the zip", "encrypted"),
    ("larger than 10 MB", "too_large"),
    ("could not be unpacked from the zip", "unpack_failed"),
    ("3 messages with no report attached", "no_report_attached"),
    ("1 message with no report attached", "no_report_attached"),
    ("more than 2,000 report files in one zip", "zip_too_many"),
]


@pytest.mark.parametrize("reason,code", KNOWN)
def test_a_known_refusal_gets_its_own_code(reason, code):
    assert codes([reason]) == [code]


@pytest.mark.parametrize("reason", [
    "bad entry",
    "line 4: unexpected token </xml>",
    "not a report",
    "",
])
def test_anything_else_is_unreadable(reason):
    """A parse failure, which is the code that may mean the reader is at fault."""
    assert codes([reason]) == ["unreadable"]


def test_a_non_string_reason_does_not_throw():
    assert codes([None, 7]) == ["unreadable", "unreadable"]


def test_every_reason_literal_in_aprf_js_still_classifies():
    """The guard: a reworded reason must not silently become 'unreadable'."""
    source = APRF_JS.read_text()
    literals = re.findall(r"skip: \[(?:label|name), '([^']+)'\]", source)
    assert literals, "no skip reason literals found; has the shape changed?"
    found = dict(zip(literals, codes(literals)))
    unclassified = [r for r, c in found.items() if c == "unreadable"]
    assert not unclassified, (
        "these reasons in aprf.js no longer match a skip code, so the usage "
        "note will report them as parse failures: %r" % unclassified
    )
