# aprf-reader

Reads APRF (Aggregate Performance Reporting) reports and tells you where the
mail you signed landed: how much reached the inbox, how much was treated as
unwanted, and how recipients reacted, day by day and per DKIM selector. It
points out the days worth a look, such as a jump in unwanted mail.

Two ways to use it, with the same results:

- **In your browser:** [aprf.dns-audit.com](https://aprf.dns-audit.com). Drop
  your reports on the page. They are read on your computer and never uploaded.
- **On the command line:** `pipx install aprf-reader`.

**Status: 0.4, beta.** Reads reports in the format of draft-ietf-mailmaint-aprf-00.

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
```

In a terminal you get an overview: for each stream, each count's latest value,
its daily range, and a trend line with a gap for each day with no report, then
the changes worth a look. Piped or written to a file with `--md`, the output
is Markdown with the full daily tables.

Point it at report files (`.json`, `.json.gz`), saved report emails (`.eml`),
mailbox exports (`.mbox`), or folders of them. The easiest way to collect reports
is a mail filter that moves them into one folder, then an export of that folder:
Gmail through [Google Takeout](https://takeout.google.com/) (pick Mail and the
label), Thunderbird with the ImportExportTools NG add-on, or Apple Mail's
Mailbox > Export Mailbox. Comcast's reports come from
`aprf-noreply@alerts.xfinity.com` with subjects starting `APRF:`. Folders are searched recursively. Files it skips are listed
on stderr and at the end of the summary, each with a reason.

Each stream (provider, signing domain, selector) gets a table with one row per
day and segment, a trend line per count, and a row for each day with no report.
A change worth a look is a day when unwanted mail or negative reactions at
least doubled and grew by 200 or more, or inbox mail fell by half and by 200
or more, against the report before. Comcast counts in steps of 100, so smaller
moves are left out as rounding.
A change in the report's `version` field is called out, since the draft uses it
to mark a change in what a category counts.

## What it does

- Read report files (`.json`, `.json.gz`, `.eml` and `.mbox`) or a folder of them, and skip
  duplicates and files it cannot read, with a reason for each.
- Summarise placement per signing domain and selector: inbox, unwanted and
  recipient reactions, by day and as a trend.
- Treat the counts as what the draft says they are: rounded into buckets, so
  they are a signal, not exact rates.
- Write a Markdown summary and a single self-contained HTML page you can open
  or share. Nothing is uploaded anywhere.
- Follow the draft as it changes. The report format lives in one module
  (`src/aprf_reader/report.py`), so a new draft revision is a small update.
- Check each report against the draft and skip, with the reason, any it
  cannot trust. One bad report in a file does not cost the rest of the file.

## Checking against the draft

Every report is checked against draft-ietf-mailmaint-aprf-00, and every
message names the section, so a list of them can go straight into a review of
the draft. On the web page, Copy for a draft review copies them as text.

**Skipped**, because the report cannot be trusted:

| Problem | Section |
|---|---|
| Not valid JSON | 5 |
| A required header field missing or of the wrong type; times not in epoch seconds | 5.2.1 |
| Not one UTC day from 00:00:00 to 23:59:59, or ending before it starts | 5.1 |
| Body not a list of segments; a segment not an object | 5.2.2 |
| A placement or engagement count that is not a whole number | 6, 7 |
| A saved email with no report attached | 8 |

**Warnings**, where the report is still read:

| Warning | Section |
|---|---|
| `sdi_used` is not N/A, N/F, or the `sdi` value as published in DNS, or is missing | 5.2.1 |
| Attachment name or subject not `<yyyymmdd>_<DKIM domain>_<DKIM selector>_<source>` | 8 |
| Message not `multipart/report`; attachment not `application/json` (or `application/gzip` for `.json.gz`) | 8, 8.1 |
| One attachment holding several reports | 8 |
| A placement key other than the four section 6 names, or an engagement key other than positive, neutral and negative | 6, 5.2.2 |
| A name or subject that writes `/` or `*` some other way, as a file name must | 8 is silent |
| The same day twice from one provider | left open, note in 8.1 |

Comcast's beta sends `sdi_used` as ` - N/A - Beta - Not in use` and a source
of `Comcast/Xfinity`, so its reports carry the `sdi_used` warning, and a
correct file name for them cannot follow section 8 exactly. A file that
repeats a report already read, such as the same report as `.json` and as
`.eml`, is counted once and summed up in one line.

## How it decides

- **The same day twice, from the same provider:** it keeps the first copy it
  reads and skips the other, saying whether the skipped copy had a different
  `version` or different counts. The draft leaves resends open (a note in
  section 8.1 asks whether a later report should overwrite or be discarded),
  and `version` is a free string with no order, so there is no defined way
  to pick the newer one. Files are read in name order, so the first copy is
  the first by file name, then by position in the file.
- **The same day from two providers:** both are kept, as separate streams.
- **Names:** signing domains and selectors compare without case, and an
  international domain matches its `xn--` form, so `bücher.example` and
  `xn--bcher-kva.example` are one stream.
- **The reporting window:** section 5.1 says a report MUST cover one UTC day,
  from 00:00:00 to 23:59:59. A report that covers more, or is shifted off the
  day boundary, is skipped with a reason that names section 5.1.
- **Counts:** whole numbers only. `100.0` reads as 100; `"100"` and negative
  numbers are refused.

## How the web page is built

`web/src/aprf.js` is a JavaScript port of the Python reader.
`tests/test_web_parity.py` runs both on the same files (JSON, gzip, saved
emails, forwarded emails, mailbox exports, duplicates and hostile text) and
requires the same JSON, Markdown and HTML output, so the page and the command
line cannot drift apart. `node web/build.js` inlines everything into one
`web/dist/index.html` whose Content Security Policy blocks every network
request, so it also works saved to disk. A merge to main that passes the
tests deploys it to Cloudflare.

## Related

- [dns-audit.com](https://dns-audit.com) checks a domain for an APRF record and
  where its reports would go.

## License

MIT
