"""Command line entry point: aprf-reader <paths...>"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import __version__
from .load import load
from .render import to_html, to_markdown, to_terminal
from .summary import to_dict


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="aprf-reader", description="Summarise APRF placement reports.")
    p.add_argument("paths", nargs="+", type=Path, help="report files (.json, .json.gz, .eml, .mbox, .zip) or folders")
    p.add_argument("--md", type=Path, help="write the summary here instead of printing it")
    p.add_argument("--html", type=Path, help="also write a self-contained HTML page here")
    p.add_argument("--markdown", action="store_true", help="print Markdown with daily tables, even in a terminal")
    p.add_argument("--json", action="store_true", help="print the summary as JSON")
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

    if args.json:
        text = json.dumps(to_dict(loaded), indent=2) + "\n"
    elif args.md or args.markdown or not sys.stdout.isatty():
        text = to_markdown(loaded)  # files and pipes get Markdown, which other tools read
    else:
        color = "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"
        text = to_terminal(loaded, color=color, width=shutil.get_terminal_size().columns)
    if args.md:
        args.md.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    if args.html:
        args.html.write_text(to_html(loaded), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
