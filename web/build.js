#!/usr/bin/env node
// build.js: inline src and ui into one self-contained page, as the Email
// Header Decoder does. dist/index.html opens from disk. Its Content-Security-Policy
// lets the page connect only to dns-audit.com, for the usage note after each use,
// and script-src allows only the hash of the one inline script. The rest of dist
// is what the static host serves beside it.
'use strict';
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const ROOT = __dirname;
const REPO = path.join(ROOT, '..');
const SITE = 'https://aprf.dns-audit.com/';
// Where the page sends its usage note (POST /api/usage on the dns-audit.com server)
const USAGE_ORIGIN = 'https://dns-audit.com';
// Synthetic, never a real report
const SAMPLE = path.join(REPO, 'tests/fixtures/sample_six_weeks.json');
const cspFor = (script) => "default-src 'none'; script-src 'sha256-" + crypto.createHash('sha256').update(script, 'utf8').digest('base64') + "'; style-src 'unsafe-inline'; img-src data:; connect-src " + USAGE_ORIGIN + "; form-action 'none'; base-uri 'none'";
// Links the page may carry. A link sends nothing until someone follows it.
const LINKS = /https?:\/\/(?!dns-audit\.com|headers\.dns-audit\.com|www\.linkedin\.com\/in\/neilanuskiewicz|github\.com\/marmot7775|pypi\.org\/project\/aprf-reader|www\.w3\.org\/2000\/svg)/;

const TITLE = 'APRF Report Reader: your inbox placement, from the provider\'s own numbers';
const DESCRIPTION = 'Free APRF report reader. See whether your mail reaches the inbox, from the mailbox provider\'s own numbers: inbox and unwanted counts day by day, what changed, and a check against the draft standard. Free, no sign-up.';
const ICON = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#2f5fcc"/><path d="M7 23l6-7 5 4 7-10" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>');

// Line endings are normalized because the browser hashes inline scripts with LF only;
// a Windows checkout with CRLF would otherwise ship a page whose CSP blocks its own script
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8').replace(/\r\n?/g, '\n');
const attr = (s) => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
const plain = (html) => html.replace(/<[^>]+>/g, '').replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/\s+/g, ' ').trim();

function version() {
  const m = /^version = "([^"]+)"/m.exec(fs.readFileSync(path.join(REPO, 'pyproject.toml'), 'utf8'));
  if (!m) throw new Error('no version in pyproject.toml');
  return m[1];
}

// The FAQ structured data is read from the page's own FAQ, so they cannot drift
function faqFrom(page) {
  const out = [];
  const re = /<details><summary>([\s\S]*?)<\/summary><p>([\s\S]*?)<\/p>/g;
  let m;
  while ((m = re.exec(page))) out.push({ '@type': 'Question', name: plain(m[1]), acceptedAnswer: { '@type': 'Answer', text: plain(m[2]) } });
  return out;
}

function jsonLd(page) {
  const graph = [
    {
      '@type': 'WebApplication',
      '@id': SITE + '#app',
      name: 'APRF Report Reader',
      url: SITE,
      description: DESCRIPTION,
      applicationCategory: 'BusinessApplication',
      operatingSystem: 'Web',
      isAccessibleForFree: true,
      offers: { '@type': 'Offer', price: '0', priceCurrency: 'USD' },
      author: { '@id': 'https://dns-audit.com/about#neil' },
      isPartOf: { '@type': 'WebSite', name: 'dns-audit.com', url: 'https://dns-audit.com/' },
    },
    {
      '@type': 'Person',
      '@id': 'https://dns-audit.com/about#neil',
      name: 'Neil Anuskiewicz',
      jobTitle: 'Email Deliverability and DNS Consultant',
      url: 'https://dns-audit.com/about',
      sameAs: ['https://www.linkedin.com/in/neilanuskiewicz/', 'https://github.com/marmot7775'],
    },
  ];
  const faq = faqFrom(page);
  if (faq.length) graph.push({ '@type': 'FAQPage', '@id': SITE + '#faq', mainEntity: faq });
  return JSON.stringify({ '@context': 'https://schema.org', '@graph': graph }, null, 2).replace(/</g, '\\u003c');
}

function head(page, csp) {
  return [
    '<meta charset="utf-8">',
    '<meta http-equiv="Content-Security-Policy" content="' + csp + '">',
    '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">',
    '<title>' + attr(TITLE) + '</title>',
    '<meta name="description" content="' + attr(DESCRIPTION) + '">',
    '<link rel="canonical" href="' + SITE + '">',
    '<link rel="icon" href="' + attr(ICON) + '">',
    '<meta name="theme-color" content="#0b1220" media="(prefers-color-scheme: dark)">',
    '<meta name="theme-color" content="#f4f6f9" media="(prefers-color-scheme: light)">',
    '<meta property="og:type" content="website">',
    '<meta property="og:site_name" content="dns-audit.com">',
    '<meta property="og:url" content="' + SITE + '">',
    '<meta property="og:title" content="' + attr(TITLE) + '">',
    '<meta property="og:description" content="' + attr(DESCRIPTION) + '">',
    '<meta property="og:image" content="' + SITE + 'og-card.png">',
    '<meta property="og:image:width" content="1200">',
    '<meta property="og:image:height" content="630">',
    '<meta property="og:image:alt" content="APRF Report Reader: inbox and unwanted counts by day, checked against the draft">',
    '<meta name="twitter:card" content="summary_large_image">',
    '<script type="application/ld+json">\n' + jsonLd(page) + '\n</script>',
  ].join('\n');
}

// The CSP also goes out as a real header, where frame-ancestors works; the
// meta copy keeps the page safe when it is opened from disk.
const headers = (csp) => '/*\n  Content-Security-Policy: ' + csp + "; frame-ancestors 'none'\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: strict-origin-when-cross-origin\n  Permissions-Policy: camera=(), microphone=(), geolocation=()\n";
const ROBOTS = 'User-agent: *\nAllow: /\n\nSitemap: ' + SITE + 'sitemap.xml\n';
const sitemap = (day) => '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n  <url><loc>' + SITE + '</loc><lastmod>' + day + '</lastmod></url>\n</urlset>\n';

function build() {
  const page = read('ui/page.html');
  if (LINKS.test(page)) throw new Error('ui/page.html links to a host outside the allowed list');
  const sample = JSON.stringify(JSON.parse(fs.readFileSync(SAMPLE, 'utf8')));
  const js = read('src/aprf.js') + '\n' +
    'window.APRF_VERSION = ' + JSON.stringify(version()) + ';\n' +
    'window.APRF_SAMPLE = ' + JSON.stringify(sample).replace(/</g, '\\u003c') + ';\n' +
    read('ui/app.js');
  if (js.includes('</script')) throw new Error('script text contains </script');
  const script = '\n' + js + '\n';
  const csp = cspFor(script);
  const body = page.replace('<!-- SCRIPTS -->', () => '<script>' + script + '</script>')
    .replace(/^<title>[^<]*<\/title>\n/, '');
  const full = '<!doctype html>\n<html lang="en">\n<head>\n' + head(page, csp) + '\n</head>\n<body>\n' + body + '\n</body>\n</html>\n';
  const dist = path.join(ROOT, 'dist');
  fs.mkdirSync(dist, { recursive: true });
  fs.writeFileSync(path.join(dist, 'index.html'), full);
  fs.writeFileSync(path.join(dist, '_headers'), headers(csp));
  fs.writeFileSync(path.join(dist, 'robots.txt'), ROBOTS);
  fs.writeFileSync(path.join(dist, 'sitemap.xml'), sitemap(new Date().toISOString().slice(0, 10)));
  // The share card; ui/og-card.html is its source, rendered at 1200 by 630
  fs.copyFileSync(path.join(ROOT, 'ui/og-card.png'), path.join(dist, 'og-card.png'));
  return { bytes: Buffer.byteLength(full), csp };
}

if (require.main === module) {
  const r = build();
  console.log('web/dist/index.html (' + r.bytes + ' bytes, opens from disk) and the host files written');
}
module.exports = { build, cspFor, faqFrom };
