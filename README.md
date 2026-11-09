# aprf-reader

Reads APRF inbox placement reports: inbox versus unwanted, day by day, per DKIM selector.

APRF is Aggregate Performance Reporting. The reader shows how much of the
mail you signed reached the inbox, how much was treated as unwanted, and how
recipients reacted. It points out the days worth a look, such as a jump in
unwanted mail.

Two ways to use it, with the same results:

- **On the web:** [aprf.dns-audit.com](https://aprf.dns-audit.com). Drop or
  paste your reports on the page.
- **On the command line:** `pipx install aprf-reader`.

**Status: 0.6, beta.** Reads reports in the format of draft-ietf-mailmaint-aprf-01.

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
aprf-reader reports/                      # overview in the terminal
aprf-reader APRF.mbox                     # a mail folder exported as mbox
aprf-reader reports/ --markdown           # Markdown with daily tables
aprf-reader reports/ --html summary.html  # and a page you can open or share
aprf-reader reports/ --json               # the summary as data, for scripts
aprf-reader --imap imap.gmail.com --user you@gmail.com --folder APRF
```

In a terminal you get an overview: for each stream, each count's latest value,
its daily range, and a trend line with a gap for each day with no report, then
the changes worth a look. Piped, or written to a file with `--md PATH`,
the output is Markdown with the full daily tables.

Point it at report files (`.json`, `.json.gz`), saved report emails (`.eml`),
mailbox exports (`.mbox`), `.zip` files of any of those, or folders of them. `-` reads one pasted report or report email from stdin. The easiest way to collect reports
is a mail filter that moves them into one folder, then an export of that folder:
Gmail through [Google Takeout](https://takeout.google.com/) (pick Mail and the
label), Thunderbird with the ImportExportTools NG add-on, or Apple Mail's
Mailbox > Export Mailbox. Comcast's reports come from
`aprf-noreply@alerts.xfinity.com` with subjects starting `APRF:`. Folders are searched recursively.

Or skip the export and read the folder straight from your mailbox over IMAP:

```bash
aprf-reader --imap imap.gmail.com --user you@gmail.com --folder APRF --since 2026-10-01
```

It opens the folder read-only and fetches with `BODY.PEEK`, so nothing is
marked read, moved or deleted. It reads messages whose subject contains
`APRF:`; `--all-mail` reads every message in the folder. The password comes
from the `APRF_IMAP_PASSWORD` environment variable, or it asks. Gmail and
Outlook.com need an app password, not your account password, and a Gmail
label is read as a folder of the same name. The connection uses TLS on port
993 and checks the server's certificate; give another port as `--imap host:port`. Files it skips are listed
on stderr and at the end of the summary, each with a reason.

Each stream (provider, signing domain, selector) gets a table with one row per
day and segment, a trend line per count, and a row for each day with no report.
A change worth a look is a day when unwanted mail or negative reactions at
least doubled, or inbox mail fell by half, against the report before, and the
count moved by at least two of the stream's steps, or by 200, whichever is
smaller. The step is the largest number every count in the stream is a
multiple of: 100 for Comcast, 1 for a provider that sends exact counts. A move
of one step is left out as rounding. With one report, or no inbox, unwanted or
negative count on two reports in a row, nothing was compared, and the reader
says so rather than calling the stream steady.
A change in the report's `version` field is called out, since the draft uses it
to mark a change in what a category counts.

## What it does

- Read report files (`.json`, `.json.gz`, `.eml`, `.mbox` and `.zip`), a folder of them, or a
  mailbox folder over IMAP, and skip
  duplicates and files it cannot read, with a reason for each.
- Summarise placement per signing domain and selector: inbox, unwanted and
  recipient reactions, by day and as a trend.
- Treat the counts as what the draft lets them be: often rounded into
  buckets whose size each provider picks (Comcast's are steps of 100), so
  they are a signal, not exact rates.
- Write a Markdown summary and a single self-contained HTML page you can open
  or share. The command line tool sends nothing anywhere; with `--imap` it
  only talks to the mail server you name.
- Follow the draft as it changes. The report format lives in one module
  (`src/aprf_reader/report.py`), so a new draft revision is a small update.
- Check each report against the draft and skip, with the reason, any it
  cannot trust. One bad report in a file does not cost the rest of the file.

## Checking against the draft

Every report is checked against draft-ietf-mailmaint-aprf-01, and every
message names the section, so a list of them can go straight into a review of
the draft. On the web page, Copy for a draft review copies them as text.

**Skipped**, because the report cannot be trusted:

| Problem | Section |
|---|---|
| Not valid JSON, or not UTF-8 (UTF-16 and UTF-32 are refused, as RFC 8259 says) | 5 |
| JSON nested more than 32 levels deep | 5 |
| No header object; `dkim_domain` or `dkim_selector` missing or empty; `report_start` or `report_end` missing or not epoch seconds; `source` or `version` a list, an object or true (a missing `source` or `version` is read, with a warning) | 5.2.1 |
| Not one UTC day from 00:00:00 to 23:59:59, or ending before it starts | 5.1 |
| Body not a list of segments; a segment not an object | 5.2.2 |
| A placement or engagement count that is not a whole number | 6, 7 |
| A saved email with no report attached | 8 |

**Warnings**, where the report is still read:

| Warning | Section |
|---|---|
| `sdi_used` is not N/A, N/F, or the `sdi` value as published in DNS, or is missing | 5.2.1 |
| `source` or `version` missing or null (a missing `source` shows as `unknown`) | 5.2.1 |
| Attachment name or subject not `<yyyymmdd>_<DKIM domain>_<DKIM selector>_<source>` | 8 |
| Message not `multipart/report`; attachment not `application/json` (or `application/gzip` for `.json.gz`) | 8, 8.1 |
| One attachment holding several reports | 8 |
| `contact_info` or `extra_info` not text or a number (ignored) | 5.2.1 |
| A placement key other than the four section 6 names, or an engagement key other than positive, neutral and negative | 6, 5.2.2 |
| A name or subject that writes `/` or `*` some other way, as a file name must | 8 is silent |
| The same day twice from one provider | 8.2 |

Comcast's beta sends `sdi_used` as ` - N/A - Beta - Not in use` and a source
of `Comcast/Xfinity`, so its reports carry the `sdi_used` warning, and a
correct file name for them cannot follow section 8 exactly. A file that
repeats a report already read, such as the same report as `.json` and as
`.eml`, is counted once and summed up in one line.

What a rolled-up report writes for the selector (header `*`) or the domain
(header `*.example.org`) in its name and subject is unsettled in -01. `!` and
an empty field (two underscores in a row) have been proposed, so the reader
accepts those and `*` there without a warning. Reports still match each other
by their header fields, never by the name, so two spellings of the same report
are treated as the same day. This gets revisited when -02 lands; the
discussion is the [thread on the -01 I-D Action](https://mailarchive.ietf.org/arch/msg/mailmaint/qA9kIVnK5-9w1uvL48x6wleAUMk/).

## How it decides

- **The same day twice, from the same provider:** the later copy replaces the
  earlier one, as section 8.2 says, even when it repeats a copy read before it
  (A, then a changed B, then A again keeps A), with a warning that says whether the
  replaced copy had a different `version` or different counts. `version`
  marks a difference, not an order, so later means read later: arrival order
  for a mailbox or mbox, and for files, name order and then position in the
  file.
- **The same day from two providers:** both are kept, as separate streams.
- **Names:** signing domains and selectors compare without case, and an
  international domain matches its `xn--` form, so `bücher.example` and
  `xn--bcher-kva.example` are one stream. A stream is headed with the domain
  and selector as its first report writes them, and the `xn--` form follows in
  brackets when it differs; `--json` gives it as `domain_key`.
- **The reporting window:** section 5.1 says a report MUST cover one UTC day,
  from 00:00:00 to 23:59:59. A report that covers more, or is shifted off the
  day boundary, is skipped with a reason that names section 5.1.
- **Files:** read in name order, by full path, whether given one by one or found in a folder,
  so the page and the command line keep the same copy of a repeated day.
- **A count a report does not carry** (classification or engagement left out, or a body with no
  segments) is missing, not zero: the table leaves it blank, a body with no segments gets a
  `no counts` row, and the trend, range and changes skip the day.
- **A missing `source` or `version`:** the report is still read, with a
  warning that names the field; a missing `source` shows as `unknown`.
- **Counts:** whole numbers only. `100.0` reads as 100; `"100"` and negative
  numbers are refused.

## How the web page is built

`web/src/aprf.js` is a JavaScript port of the Python reader.
`tests/test_web_parity.py` runs both on the same files (JSON, gzip, saved
emails, forwarded emails, mailbox exports, duplicates and hostile text) and
requires the same JSON, Markdown and HTML output, so the page and the command
line cannot drift apart. `node web/build.js` inlines everything into one
`web/dist/index.html` whose Content Security Policy allows one destination, the
usage notes to dns-audit.com (one when the page opens, one after each read), and it also works saved to disk. A merge to main that passes the
tests deploys it to Cloudflare.

## Related

- [dns-audit.com](https://dns-audit.com) checks a domain for an APRF record and
  where its reports would go.

## License

MIT
