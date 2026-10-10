# aprf-reader

Reads APRF (Aggregate Performance Reporting) reports and tells you where the
mail you signed landed: how much reached the inbox, how much was treated as
unwanted, and how recipients reacted, day by day and per DKIM selector.

**Status: in development.** Not usable yet. Watch or star the repo to follow along.

## What APRF is

APRF is a proposed standard for a daily report from a mailbox provider about the
mail you signed with DKIM. It is an IETF working group draft,
[draft-ietf-mailmaint-aprf](https://datatracker.ietf.org/doc/draft-ietf-mailmaint-aprf/),
adopted by the Mail Maintenance (MAILMAINT) working group in October 2026. Reports
arrive by email as small JSON files, one per day per signing domain and selector.
Comcast sends them today in a beta for comcast.net mail.

A plain-language introduction: [What APRF inbox placement reports tell you, and
what they don't](https://dns-audit.com/articles/aprf).

## What it will do

- Read report files (`.json` and `.json.gz`) or a folder of them, and skip
  duplicates and files it cannot read, with a reason for each.
- Summarise placement per signing domain and selector: inbox, unwanted and
  recipient reactions, by day and as a trend.
- Treat the counts as what the draft says they are: rounded into buckets, so
  they are a signal, not exact rates.
- Write a Markdown summary and a single self-contained HTML page you can open
  or share. Nothing is uploaded anywhere.
- Follow the draft as it changes. The report format lives in one module, so a
  new draft revision is a small update.

## Related

- [dns-audit.com](https://dns-audit.com) checks a domain for an APRF record and
  where its reports would go.

## License

MIT
