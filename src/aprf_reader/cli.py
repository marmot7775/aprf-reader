"""Command line entry point: aprf-reader <paths...>"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path

from . import __version__
from .imap import ImapError, fetch
from .load import load
from .render import to_html, to_markdown, to_terminal
from .summary import to_dict


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="aprf-reader", description="Summarise APRF placement reports.")
    p.add_argument("paths", nargs="*", type=Path,
                   help="report files (.json, .json.gz, .eml, .mbox, .zip) or folders; - reads a pasted report or email from stdin")
    p.add_argument("--md", type=Path, help="write the summary here instead of printing it")
    p.add_argument("--html", type=Path, help="also write a self-contained HTML page here")
    p.add_argument("--markdown", action="store_true", help="print Markdown with daily tables, even in a terminal")
    p.add_argument("--json", action="store_true", help="print the summary as JSON")
    imap = p.add_argument_group("reading a mailbox over IMAP (read-only: nothing is marked read, moved or deleted)")
    imap.add_argument("--imap", metavar="HOST[:PORT]", help="IMAP server, such as imap.gmail.com; TLS on port 993 unless given")
    imap.add_argument("--user", help="IMAP login name; the password is read from APRF_IMAP_PASSWORD or asked for")
    imap.add_argument("--folder", default="INBOX", help="folder to read (default INBOX)")
    imap.add_argument("--since", type=date.fromisoformat, metavar="YYYY-MM-DD", help="only mail received on or after this day")
    imap.add_argument("--all-mail", action="store_true", help='read every message in the folder, not only subjects with "APRF:"')
    p.add_argument("--version", action="version", version=f"aprf-reader {__version__}")
    args = p.parse_args(argv)
    if not args.paths and not args.imap:
        p.error("give report files or folders, or --imap")
    if args.imap and not args.user:
        p.error("--imap needs --user")

    paths = [x for x in args.paths if str(x) != "-"]
    for path in paths:
        if not path.exists():
            p.error(f"no such file or folder: {path}")
    pasted = sys.stdin.buffer.read() if len(paths) < len(args.paths) else None
    messages = []
    if args.imap:
        host, _, port = args.imap.partition(":")
        if port and not port.isdigit():
            p.error(f"--imap port is not a number: {port}")
        password = os.environ.get("APRF_IMAP_PASSWORD") or getpass.getpass(f"IMAP password for {args.user}: ")
        try:
            # Fetched in full before reading, so a dropped connection leaves no half summary
            messages = list(fetch(host, args.user, password, args.folder, int(port or 993), args.since, args.all_mail))
        except (ImapError, OSError) as e:
            print(f"aprf-reader: {args.imap}: {e}", file=sys.stderr)
            return 2
        print(f"read {len(messages)} message{'s' if len(messages) != 1 else ''} from {args.folder} on {host}", file=sys.stderr)
    loaded = load(paths, pasted, messages, f"imap://{args.imap}/{args.folder}" if args.imap else "mailbox")
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
