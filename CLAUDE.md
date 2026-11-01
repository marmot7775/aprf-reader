# aprf-reader

A reader for APRF reports: the Python package and CLI in src, and the
same reader as one static page in web, live at aprf.dns-audit.com.

## Rules

The header and footer are the canonical copy from dns-audit's
docs/site-chrome.html. Change them there first, then copy them here;
tests/test_web_chrome.py holds the lists, and tools/check_chrome.py in
the dns-audit repo checks the three live sites.

Dropped or pasted reports are never sent anywhere. The usage note to
dns-audit.com is the page's only network call, and it never carries
report content, domains, or addresses.
