"""The header and footer are dns-audit's canonical chrome
(docs/site-chrome.html in that repo). These lists are that copy, with the
three repo links pointing here. Change the chrome there first."""

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"
PAGE = (WEB / "ui" / "page.html").read_text(encoding="utf-8")
REPO = "https://github.com/marmot7775/aprf-reader"
# Built from pieces so the address appears in the page only, never here
ADDRESS = "dns" + "@" + "dns-audit.com"

NAV = [
    ("DNS Audit", "https://dns-audit.com/"),
    ("Header Decoder", "https://headers.dns-audit.com/"),
    ("APRF Reader", "https://aprf.dns-audit.com/"),
    ("Articles", "https://dns-audit.com/articles/"),
    ("About", "https://dns-audit.com/about"),
]
FOOTER = [
    ("Articles", "https://dns-audit.com/articles/"),
    ("About", "https://dns-audit.com/about"),
    ("Privacy", "https://dns-audit.com/privacy"),
    ("GitHub", REPO),
    ("Email " + ADDRESS, "mailto:" + ADDRESS),
    ("message me on LinkedIn", "https://www.linkedin.com/in/neilanuskiewicz/"),
    ("Security policy", REPO + "/security/policy"),
    ("MIT License", REPO + "/blob/main/LICENSE"),
]


class Links(HTMLParser):
    """Collects each link inside the first <nav> and inside <footer>, with
    its attributes and text. The tool links carry a long and a short label;
    the text kept is the long one, which is what check_chrome.py reads."""

    def __init__(self):
        super().__init__()
        self.nav, self.footer = [], []
        self.in_nav = self.in_footer = self.nav_done = False
        self.link = None
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self.skip:
            self.skip += 1
        elif tag == "nav" and not self.nav_done and not self.in_footer:
            self.in_nav = True
        elif tag == "footer":
            self.in_footer = True
        elif tag == "a" and (self.in_nav or self.in_footer):
            self.link = {"attrs": attrs, "text": ""}
        elif tag == "span" and self.link is not None and attrs.get("class") == "nav-short":
            self.skip = 1

    def handle_endtag(self, tag):
        if self.skip:
            self.skip -= 1
        elif tag == "a" and self.link is not None:
            self.link["text"] = " ".join(self.link["text"].split())
            (self.nav if self.in_nav else self.footer).append(self.link)
            self.link = None
        elif tag == "nav" and self.in_nav:
            self.in_nav, self.nav_done = False, True
        elif tag == "footer":
            self.in_footer = False

    def handle_data(self, data):
        if self.link is not None and not self.skip:
            self.link["text"] += data


def links():
    parser = Links()
    parser.feed(PAGE)
    return parser


def pairs(found):
    return [(link["text"], link["attrs"].get("href")) for link in found]


def test_header_nav_is_the_canonical_list():
    assert pairs(links().nav) == NAV


def test_footer_is_the_canonical_list_with_the_repo_links_pointing_here():
    assert pairs(links().footer) == FOOTER


def test_only_the_aprf_reader_link_is_marked_current():
    marked = [link for link in links().nav
              if "aria-current" in link["attrs"] or "nav-link-active" in link["attrs"].get("class", "").split()]
    assert len(marked) == 1
    assert marked[0]["text"] == "APRF Reader"
    assert marked[0]["attrs"].get("aria-current") == "page"
    assert "nav-link-active" in marked[0]["attrs"]["class"].split()


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_build_csp_carries_two_sha256_sources():
    out = subprocess.run(
        ["node", "-e", "const b = require('./build.js'); const r = b.build();"
         " console.log(JSON.stringify({ csp: r.csp, theme: b.THEME_SCRIPT }))"],
        cwd=WEB, capture_output=True, text=True, check=True)
    built = json.loads(out.stdout.strip().splitlines()[-1])
    sources = re.search(r"script-src ([^;]*)", built["csp"]).group(1).split(" ")
    assert len(sources) == 2
    for source in sources:
        assert re.fullmatch(r"'sha256-[A-Za-z0-9+/]+=*'", source)
    # The head script only applies a saved choice; it never writes or sends.
    assert "localStorage.getItem('theme')" in built["theme"]
    assert not re.search(r"setItem|fetch|XMLHttpRequest", built["theme"])
    html = (WEB / "dist" / "index.html").read_text(encoding="utf-8")
    assert re.search(r"<head>[\s\S]*<script>[\s\S]*</head>", html)
