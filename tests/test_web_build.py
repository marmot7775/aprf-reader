"""web/build.js strips tags from the FAQ text for the JSON-LD completely (CodeQL CS-1)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def plain(html: str) -> str:
    code = "process.stdout.write(JSON.stringify(require(process.argv[1]).plain(process.argv[2])))"
    out = subprocess.run([NODE, "-e", code, str(ROOT / "web/build.js"), html], check=True, capture_output=True).stdout
    return json.loads(out)


def test_plain_strips_ordinary_tags():
    assert plain("a <b>bold</b> &amp; <i>plain</i> &quot;x&quot;") == 'a bold & plain "x"'


def test_plain_leaves_no_script_tag_when_tags_nest():
    assert "<script" not in plain("<scr<b>ipt>alert(1)</scr</b>ipt>")
    assert "<" not in plain("x <<b>script>y")
