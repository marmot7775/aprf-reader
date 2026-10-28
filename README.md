# aprf-reader

Reads APRF (Aggregate Performance Reporting) reports and tells you where the
mail you signed landed: how much reached the inbox, how much was treated as
unwanted, and how recipients reacted, day by day and per DKIM selector.

**Status: 0.1, beta.** Reads reports in the format of draft-ietf-mailmaint-aprf-00.

## What APRF is

APRF is a proposed standard for a daily report from a mailbox provider about the
mail you signed with DKIM. It is an IETF working group draft,
[draft-ietf-mailmaint-aprf](https://datatracker.ietf.org/doc/draft-ietf-mailmaint-aprf/),
adopted by the Mail Maintenance (MAILMAINT) working group in October 2026. Reports
arrive by email as small JSON files, one per day per signing domain and selector.
Comcast sends them today in a beta for comcast.net mail.

A plain-language introduction: [What APRF inbox placement reports tell you, and
what they don't](https://dns-audit.com/articles/aprf).

## Use it

Python 3.11 or later, no other dependencies. Install with
[pipx](https://pipx.pypa.io/), which keeps command line tools in their own
environment, or with pip (`pip3` on some systems) inside a virtual environment.

```bash
pipx install aprf-reader
aprf-reader reports/                      # Markdown summary to the terminal
aprf-reader reports/ --html summary.html  # and a page you can open or share
```

Point it at report files (`.json`, `.json.gz`), saved report emails (`.eml`),
or folders of them. Folders are searched recursively. Files it skips are listed
on stderr and at the end of the summary, each with a reason.

Each stream (provider, signing domain, selector) gets a table with one row per
day and segment, a trend line per count, and a row for each day with no report.
A change in the report's `version` field is called out, since the draft uses it
to mark a change in what a category counts.

## What it does

- Read report files (`.json`, `.json.gz` and `.eml`) or a folder of them, and skip
  duplicates and files it cannot read, with a reason for each.
- Summarise placement per signing domain and selector: inbox, unwanted and
  recipient reactions, by day and as a trend.
- Treat the counts as what the draft says they are: rounded into buckets, so
  they are a signal, not exact rates.
- Write a Markdown summary and a single self-contained HTML page you can open
  or share. Nothing is uploaded anywhere.
- Follow the draft as it changes. The report format lives in one module
  (`src/aprf_reader/report.py`), so a new draft revision is a small update.
- When the same day arrives twice, keep the first copy and say so. The draft
  has not yet decided whether a resend replaces the original.

## Related

- [dns-audit.com](https://dns-audit.com) checks a domain for an APRF record and
  where its reports would go.

## License

MIT
