"""Command line entry point: aprf-reader <paths...>"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .load import load
from .render import to_html, to_markdown


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="aprf-reader", description="Summarise APRF placement reports.")
    p.add_argument("paths", nargs="+", type=Path, help="report files (.json, .json.gz, .eml, .mbox) or folders")
    p.add_argument("--md", type=Path, help="write the Markdown summary here instead of printing it")
    p.add_argument("--html", type=Path, help="also write a self-contained HTML page here")
    p.add_argument("--version", action="version", version=f"aprf-reader {__version__}")
    args = p.parse_args(argv)

    for path in args.paths:
        if not path.exists():
            p.error(f"no such file or folder: {path}")
    loaded = load(args.paths)
    for where, reason in loaded.skipped:
        print(f"skipped {where}: {reason}", file=sys.stderr)
    if not loaded.reports:
        print("no APRF reports found", file=sys.stderr)
        return 1

    md = to_markdown(loaded)
    if args.md:
        args.md.write_text(md, encoding="utf-8")
    else:
        sys.stdout.write(md)
    if args.html:
        args.html.write_text(to_html(loaded), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
