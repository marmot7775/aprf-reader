// aprf.js: the report format, file reading, and the summary, for the browser.
// A port of src/aprf_reader (report.py, load.py, summary.py, render.py).
// tests/test_web_parity.py runs both on the same files and requires the same
// JSON and Markdown, so the page and the command line cannot drift apart.
'use strict';
(function (root) {
  const DRAFT = 'draft-ietf-mailmaint-aprf-00';
  const PLACEMENT_NAMES = ['inbox', 'unwanted'];
  const ENGAGEMENT_NAMES = ['positive', 'neutral', 'negative'];
  // sdi_used values that mean no SDI was applied (section 5.2.1), compared on
  // the first word without case or trailing punctuation
  const NO_SDI = ['', 'N/A', 'N/F'];
  const usesSdi = (v) => !NO_SDI.includes(v.split(' ')[0].replace(/[;,.]+$/, '').toUpperCase());
  // JavaScript numbers are exact only up to here
  const MAX_COUNT = Number.MAX_SAFE_INTEGER;
  // Counts where a jump up is bad news, or a drop is
  const CHANGES = { unwanted: 'rise', negative: 'rise', inbox: 'fall' };
  const MIN_MOVE = 200;
  const SUFFIXES = ['.json', '.json.gz', '.eml', '.mbox'];
  // A daily report is a few KB; this stops a stray huge file or gzip bomb
  const MAX_BYTES = 10 * 1024 * 1024;
  const DAY_MS = 86400000;
  const NO_COUNTS = 'These reports carry no counts.';
  const CAVEAT = 'Counts are bucket upper bounds chosen by each provider: a ceiling, not an exact figure. ' +
    'Placement counts mail received that day; engagement counts actions taken that day on mail ' +
    'from any day. Dividing one count by another gives a ratio nobody measured.';

  class ReportError extends Error {}
  // Placement names section 6 gives as examples; engagement names section 5.2.2 lists
  const NAMED_CLASSIFICATION = ['inbox', 'unwanted', 'forwarded', 'promotional'];
  // sdi as published in DNS (section 4): a header field name, a comma, one separator character
  const SDI_FORMAT = /^[!-9;-~]+,\S$/;
  const ONE_DAY = 'not one UTC day from 00:00:00 to 23:59:59, which draft section 5.1 requires';

  // ---- report.py ----

  const isInt = (n) => typeof n === 'number' && Number.isInteger(n);
  const text = (v) => (v === null || v === undefined ? '' : String(v));

  function dayOf(epoch) {
    const d = new Date(epoch * 1000);
    const y = d.getUTCFullYear();
    // Python's date stops at year 9999; match it so both refuse the same files
    if (isNaN(y) || y < 1 || y > 9999) throw new ReportError('header.report_end is out of range (section 5.2.1): ' + epoch);
    return d.toISOString().slice(0, 10);
  }

  function counts(value, where, section) {
    if (value === null || value === undefined) return {};
    if (typeof value !== 'object' || Array.isArray(value)) throw new ReportError(where + ' is not an object (' + section + ')');
    const out = {};
    for (const [name, n] of Object.entries(value)) {
      if (!isInt(n) || n < 0 || n > MAX_COUNT) throw new ReportError(where + '.' + name + ' is not a count (' + section + '): ' + JSON.stringify(n));
      out[name] = n;
    }
    return out;
  }

  function segment(item, i) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) throw new ReportError('body[' + i + '] is not an object (section 5.2.2)');
    const seg = item.segment;
    let path;
    // Section 5.2.2 shows a string, the samples a list
    if (seg === null || seg === undefined) path = [];
    else if (typeof seg === 'string') path = [seg];
    else if (Array.isArray(seg) && seg.every((s) => typeof s === 'string')) path = seg.slice();
    else throw new ReportError('body[' + i + '].segment is not a string or list of strings (section 5.2.2)');
    return {
      path,
      classification: counts(item.classification, 'body[' + i + '].classification', 'section 6'),
      engagement: counts(item.engagement, 'body[' + i + '].engagement', 'section 7'),
    };
  }

  function report(obj) {
    if (!obj || typeof obj !== 'object' || Array.isArray(obj) || !obj.header || typeof obj.header !== 'object' || Array.isArray(obj.header)) {
      throw new ReportError('no header object (section 5.2.1)');
    }
    const h = obj.header;
    for (const name of ['dkim_domain', 'dkim_selector']) {
      if (typeof h[name] !== 'string' || !h[name]) throw new ReportError('header.' + name + ' is missing (section 5.2.1)');
    }
    for (const name of ['report_start', 'report_end']) {
      const v = h[name];
      if (!isInt(v)) throw new ReportError('header.' + name + ' is not an epoch time (section 5.2.1)');
      // Seconds past 1e11 are beyond the year 5138; up to 1e14 that is milliseconds
      if (Math.abs(v) >= 1e11 && Math.abs(v) < 1e14) throw new ReportError('header.' + name + ' looks like milliseconds, not epoch seconds (section 5.2.1): ' + v);
    }
    if (h.report_end < h.report_start) throw new ReportError('header.report_end is before report_start (section 5.1)');
    const day = dayOf(h.report_end);
    if (h.report_start % 86400 !== 0 || h.report_end !== h.report_start + 86399) throw new ReportError(ONE_DAY);
    const body = obj.body === undefined ? [] : obj.body;
    if (!Array.isArray(body)) throw new ReportError('body is not a list of segments (section 5.2.2)');
    const segments = body.map(segment);
    return {
      source: text(h.source) || 'unknown',
      dkim_domain: h.dkim_domain,
      dkim_selector: h.dkim_selector,
      report_start: h.report_start,
      report_end: h.report_end,
      version: text(h.version),
      // Comcast's beta sends " - N/A - Beta - Not in use"
      sdi_used: text(h.sdi_used).replace(/^[ -]+|[ -]+$/g, ''),
      contact_info: text(h.contact_info),
      extra_info: text(h.extra_info),
      segments,
      warnings: reportWarnings(h, segments),
      // Section 8: the report's date is its ending date, in UTC
      day,
    };
  }

  // DNS names compare without case, and bücher.example is xn--bcher-kva.example.
  // Only plain letters, digits, dots and hyphens are converted, as in report.py.
  function nameKey(domain) {
    const d = domain.toLowerCase();
    if (/^[\x00-\x7f]*$/.test(d) || !/^[\p{L}\p{N}.-]+$/u.test(d)) return d;
    try { return new URL('http://' + d + '/').hostname; } catch (e) { return d; }
  }
  const streamKey = (r) => [r.source, nameKey(r.dkim_domain), r.dkim_selector.toLowerCase()];
  function reportWarnings(h, segments) {
    const out = [];
    if (!('sdi_used' in h)) out.push(['no sdi_used field; section 5.2.1 requires N/A when no SDI is used', '']);
    else {
      const raw = h.sdi_used;
      if (!(raw === 'N/A' || raw === 'N/F' || (typeof raw === 'string' && SDI_FORMAT.test(raw)))) {
        out.push(['sdi_used is not N/A, N/F, or the sdi value as published in DNS (section 5.2.1)', JSON.stringify(raw)]);
      }
    }
    const keys = (attr) => [...new Set(segments.flatMap((g) => Object.keys(g[attr])))].sort(cmp);
    for (const k of keys('classification')) {
      if (!NAMED_CLASSIFICATION.includes(k)) out.push(['classification key ' + JSON.stringify(k) + ' is not one section 6 names (inbox, unwanted, forwarded, promotional); read as given', '']);
    }
    for (const k of keys('engagement')) {
      if (!ENGAGEMENT_NAMES.includes(k)) out.push(['engagement key ' + JSON.stringify(k) + ' is not positive, neutral or negative (section 5.2.2); read as given', '']);
    }
    return out;
  }

  const keyOf = (r) => JSON.stringify(streamKey(r).concat([r.day]));

  // Each report in a document, or the reason it cannot be read, so one bad
  // report does not cost the rest of the file
  function parseItems(bytes) {
    let doc;
    try {
      doc = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
    } catch (e) {
      throw new ReportError(e instanceof RangeError ? 'JSON nested too deeply' : 'not valid JSON, which section 5 requires: ' + e.message);
    }
    const items = Array.isArray(doc) ? doc : [doc];
    if (!items.length) throw new ReportError('empty list');
    return items.map((o) => {
      try { return report(o); } catch (e) { if (e instanceof ReportError) return e; throw e; }
    });
  }

  function parse(bytes) {
    const items = parseItems(bytes);
    for (const r of items) if (r instanceof ReportError) throw r;
    return items;
  }

  // ---- load.py ----

  // Bytes to a string with one char per byte, so MIME parsing never mangles binary
  function latin1(bytes) {
    let s = '';
    for (let i = 0; i < bytes.length; i += 32768) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 32768));
    return s;
  }
  const bytesOf = (s) => Uint8Array.from(s, (c) => c.charCodeAt(0) & 255);
  const isGzip = (b) => b.length > 1 && b[0] === 0x1f && b[1] === 0x8b;

  async function gunzip(bytes) {
    const reader = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip')).getReader();
    const chunks = [];
    let size = 0;
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.length;
        if (size > MAX_BYTES) throw new ReportError('larger than 10 MB unpacked');
        chunks.push(value);
      }
    } catch (e) {
      reader.cancel().catch(() => {});
      throw e instanceof ReportError ? e : new ReportError('not a readable gzip file');
    }
    const out = new Uint8Array(size);
    let at = 0;
    for (const c of chunks) { out.set(c, at); at += c.length; }
    return out;
  }

  function params(value) {
    const out = {};
    const re = /;\s*([\w.*-]+)\s*=\s*("(?:[^"\\]|\\.)*"|[^;\s]*)/g;
    let m;
    while ((m = re.exec(value))) {
      let v = m[2];
      if (v.startsWith('"')) v = v.slice(1, -1).replace(/\\(.)/g, '$1');
      let k = m[1].toLowerCase();
      // RFC 2231: filename*=utf-8''name%20here
      if (k.endsWith('*')) {
        k = k.slice(0, -1);
        try { v = decodeURIComponent(v.replace(/^[^']*'[^']*'/, '')); } catch (e) { /* keep raw */ }
      }
      out[k] = v;
    }
    return out;
  }

  function decodeQP(s) {
    return s.replace(/=\r?\n/g, '').replace(/=([0-9A-Fa-f]{2})/g, (_, h) => String.fromCharCode(parseInt(h, 16)));
  }

  // One email (as a latin1 string) to its leaf parts, descending into attached emails
  function mimeParts(raw, out, depth, owner) {
    if (depth > 20) return out;
    const split = /\r?\n\r?\n/.exec(raw);
    const head = split ? raw.slice(0, split.index) : raw;
    const body = split ? raw.slice(split.index + split[0].length) : '';
    const fields = {};
    for (const line of head.replace(/\r?\n[ \t]+/g, ' ').split(/\r?\n/)) {
      const c = line.indexOf(':');
      if (c > 0) {
        const name = line.slice(0, c).trim().toLowerCase();
        if (!(name in fields)) fields[name] = line.slice(c + 1).trim();
      }
    }
    const ctypeField = fields['content-type'] || 'text/plain';
    const ctype = ctypeField.split(';')[0].trim().toLowerCase() || 'text/plain';
    // shortcut: subjects are not decoded from RFC 2047 encoded words; a report subject is plain ASCII
    if (!owner) owner = { subject: (fields.subject || '').split(/\s+/).filter(Boolean).join(' '), ctype };
    const cp = params(ctypeField);
    if (ctype.startsWith('multipart/') && cp.boundary) {
      const esc = cp.boundary.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const re = new RegExp('(?:^|\\r?\\n)--' + esc + '(--)?[ \\t]*(?:\\r?\\n|$)', 'g');
      const marks = [];
      let m;
      while ((m = re.exec(body))) {
        marks.push({ start: m.index, end: m.index + m[0].length, close: !!m[1] });
        if (m[1]) break;
        if (m[0].length === 0) re.lastIndex++;
      }
      // Text before the first delimiter is the preamble; after the closing one, the epilogue
      for (let i = 0; i < marks.length; i++) {
        if (marks[i].close) break;
        // No closing delimiter: the last part runs to the end, as Python reads it
        mimeParts(body.slice(marks[i].end, i + 1 < marks.length ? marks[i + 1].start : body.length), out, depth + 1, owner);
      }
      return out;
    }
    if (ctype === 'message/rfc822') return mimeParts(body, out, depth + 1, null);
    const disp = fields['content-disposition'] || '';
    const dp = params(disp);
    const enc = (fields['content-transfer-encoding'] || '').trim().toLowerCase();
    let data;
    if (enc === 'base64') {
      try { data = bytesOf(atob(body.replace(/[^A-Za-z0-9+/=]/g, '').replace(/=+(?=[^=])/g, ''))); } catch (e) { data = new Uint8Array(0); }
    } else if (enc === 'quoted-printable') data = bytesOf(decodeQP(body));
    else data = bytesOf(body);
    const name = dp.filename || cp.name || '';
    out.push({
      name,
      filename: name.toLowerCase(),
      ctype,
      owner,
      attachment: disp.split(';')[0].trim().toLowerCase() === 'attachment',
      data,
    });
    return out;
  }

  // Yield [label, report JSON bytes] for each report attached to one email
  async function attachments(raw, label) {
    const found = [];
    for (const p of mimeParts(raw, [], 0, null)) {
      const where = label + ' (' + (p.filename || p.ctype) + ')';
      if (p.data.length > MAX_BYTES) throw new ReportError(where + ' is larger than 10 MB');
      const mail = (gz) => ({ name: p.name, ctype: p.ctype, gz, subject: p.owner.subject, msgCtype: p.owner.ctype });
      if (p.filename.endsWith('.gz') || p.ctype === 'application/gzip' || p.ctype === 'application/x-gzip' || isGzip(p.data)) {
        found.push([where, await gunzip(p.data), mail(true)]);
      } else if (p.filename.endsWith('.json') || p.ctype === 'application/json' || (p.attachment && /^\s*[[{]/.test(latin1(p.data.subarray(0, 64))))) {
        // By name or type, or an attachment of unhelpful type that is a JSON array or object
        found.push([where, p.data, mail(false)]);
      }
    }
    return found;
  }

  // Apple Mail exports a folder "Name.mbox" holding a file named just "mbox"
  const baseName = (name) => name.split('/').pop().toLowerCase();
  const isMbox = (name) => baseName(name) === 'mbox' || baseName(name).endsWith('.mbox');
  const wanted = (name) => SUFFIXES.some((s) => baseName(name).endsWith(s)) || isMbox(name);

  // Like Python's mailbox.mbox: every line starting "From " opens a message
  function mboxMessages(raw) {
    const starts = [];
    const re = /^From /gm;
    let m;
    while ((m = re.exec(raw))) starts.push(m.index);
    return starts.map((at, i) => {
      const msg = raw.slice(at, i + 1 < starts.length ? starts[i + 1] : raw.length);
      const nl = msg.indexOf('\n');
      return nl < 0 ? '' : msg.slice(nl + 1);
    });
  }

  // Counts compared without regard to key order, as Python compares dicts
  const canon = (segs) => JSON.stringify(segs.map((g) => [g.path,
    ...['classification', 'engagement'].map((a) => Object.keys(g[a]).sort().map((k) => [k, g[a][k]]))]));
  function differs(kept, dropped) {
    if (kept.version !== dropped.version) return ', version ' + (kept.version || 'none') + ' kept, version ' + (dropped.version || 'none') + ' dropped';
    return canon(kept.segments) !== canon(dropped.segments) ? ', with different counts' : ', same counts';
  }

  const DUPLICATE_DAY = 'the same day came twice from one provider; the first copy read was kept. ' +
    'The draft leaves this open (a note in section 8.1)';
  // Characters a file name cannot hold on common systems; "Comcast/Xfinity" and a "*" roll-up
  // selector both carry one, and section 8 only says to remove spaces
  const UNSAFE = '/\\:*?"<>|';
  const NAME_UNSAFE = 'attachment name stands in for a character a file name cannot hold, such as / or *; ' +
    'section 8 does not say how to write those';
  const SUBJECT_UNSAFE = 'subject stands in for / or * as a file name must, though section 8 gives the source ' +
    'and selector as they are';
  // Python lowercases the same way (str.lower)
  const fold = (t) => t.toLowerCase();
  function loose(expected, actual) {
    const pattern = [...fold(expected)].map((c) => (UNSAFE.includes(c) ? '.' : c.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))).join('');
    return new RegExp('^' + pattern + '$', 'su').test(fold(actual));
  }
  // Section 8: the ending date, then domain, selector and source with spaces removed
  const reportName = (r) => r.day.replace(/-/g, '') + '_' + r.dkim_domain + '_' + r.dkim_selector + '_' + r.source.replace(/ /g, '');

  function mailWarnings(mail, items) {
    const out = [];
    if (mail.msgCtype !== 'multipart/report') out.push(['message Content-Type is not multipart/report (section 8)', mail.msgCtype]);
    const want = mail.gz ? 'application/gzip' : 'application/json';
    if (mail.ctype !== want) out.push(['attachment Content-Type is not application/json, or application/gzip when compressed (sections 8 and 8.1)', mail.ctype]);
    if (items.length > 1) out.push(['one attachment holds several reports; the section 8 name and subject fit one', items.length + ' reports']);
    else if (!(items[0] instanceof ReportError)) {
      const base = reportName(items[0]);
      const file = base + (mail.gz ? '.json.gz' : '.json');
      if (fold(mail.name) === fold(file)) { /* matches */ } else if (loose(file, mail.name)) out.push([NAME_UNSAFE, mail.name + '; section 8 gives ' + file]);
      else out.push(['attachment name does not follow section 8: <yyyymmdd>_<DKIM domain>_<DKIM selector>_<source>.json', (mail.name || '(none)') + '; expected ' + file]);
      const subj = 'APRF: ' + base;
      if (fold(mail.subject) === fold(subj)) { /* matches */ } else if (loose(subj, mail.subject)) out.push([SUBJECT_UNSAFE, mail.subject + '; section 8 gives ' + subj]);
      else out.push(['subject does not follow section 8: APRF: <yyyymmdd>_<DKIM domain>_<DKIM selector>_<source>', (mail.subject || '(none)') + '; expected ' + subj]);
    }
    return out;
  }

  async function sha256(bytes) {
    const subtle = (root.crypto && root.crypto.subtle) || require('crypto').webcrypto.subtle;
    return latin1(new Uint8Array(await subtle.digest('SHA-256', bytes)));
  }

  // files: [{ name, bytes }] in the order to read them. Returns { reports, skipped }.
  async function load(files) {
    // warnings: [warning, detail, where]; copies: files whose report bytes were already read
    const out = { reports: [], skipped: [], warnings: [], copies: 0 };
    const parsed = new Map();
    const seenKeys = new Map();

    async function add(where, data, mail) {
      const digest = await sha256(data);
      const copy = parsed.has(digest);
      if (!copy) {
        try { parsed.set(digest, parseItems(data)); } catch (e) {
          if (!(e instanceof ReportError)) throw e;
          parsed.set(digest, e);
        }
      }
      const items = parsed.get(digest);
      // An email is checked even when its report was already read from another file
      if (mail && !(items instanceof ReportError)) for (const [w, d] of mailWarnings(mail, items)) out.warnings.push([w, d, where]);
      if (copy) { out.copies++; return; }
      if (items instanceof ReportError) { out.skipped.push([where, items.message]); return; }
      items.forEach((r, i) => {
        const label = items.length > 1 ? where + ' report ' + (i + 1) : where;
        if (r instanceof ReportError) { out.skipped.push([label, r.message]); return; }
        for (const [w, d] of r.warnings) out.warnings.push([w, d, label]);
        // The draft leaves open whether a resend replaces the first (an open note in
        // section 8.1), and its version is a free string with no order; keep the first
        const k = keyOf(r);
        if (seenKeys.has(k)) {
          const [firstLabel, first] = seenKeys.get(k);
          out.warnings.push([DUPLICATE_DAY, firstLabel + ' kept' + differs(first, r), label]);
          return;
        }
        seenKeys.set(k, [label, r]);
        out.reports.push(r);
      });
    }

    for (const { name, bytes } of files) {
      try {
        if (isMbox(name)) {
          // An mbox can be large, so only its attachments are size-checked
          let without = 0;
          const msgs = mboxMessages(latin1(bytes));
          for (let i = 0; i < msgs.length; i++) {
            const label = name + ' message ' + (i + 1);
            let found;
            try { found = await attachments(msgs[i], label); } catch (e) {
              if (!(e instanceof ReportError)) throw e;
              out.skipped.push([label, e.message]);
              continue;
            }
            if (!found.length) without++;
            for (const [w, d, m] of found) await add(w, d, m);
          }
          if (without) out.skipped.push([name, without + ' message' + (without === 1 ? '' : 's') + ' with no report attached']);
          continue;
        }
        if (bytes.length > MAX_BYTES) throw new ReportError('larger than 10 MB');
        let payloads;
        if (baseName(name).endsWith('.eml')) {
          payloads = await attachments(latin1(bytes), name);
          if (!payloads.length) throw new ReportError('no report attachment, which section 8 calls for');
        } else if (isGzip(bytes)) payloads = [[name, await gunzip(bytes), null]];
        else payloads = [[name, bytes, null]];
        for (const [w, d, m] of payloads) await add(w, d, m);
      } catch (e) {
        if (!(e instanceof ReportError)) throw e;
        out.skipped.push([name, e.message]);
      }
    }
    return out;
  }

  // ---- summary.py ----

  const ms = (day) => Date.parse(day + 'T00:00:00Z');
  const addDays = (day, n) => new Date(ms(day) + n * DAY_MS).toISOString().slice(0, 10);
  const between = (a, b) => Math.round((ms(b) - ms(a)) / DAY_MS);
  const fmt = (n) => n.toLocaleString('en-US');
  const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

  function names(byDay, attr, order) {
    const seen = new Set();
    for (const r of byDay.values()) for (const s of r.segments) for (const k of Object.keys(s[attr])) seen.add(k);
    return order.filter((k) => seen.has(k)).concat([...seen].filter((k) => !order.includes(k)).sort(cmp));
  }

  function stream(source, domain, selector, byDay) {
    const days = [...byDay.keys()].sort(cmp);
    const s = { source, domain, selector, byDay, days, first: days[0], last: days[days.length - 1] };
    s.missing = between(s.first, s.last) + 1 - days.length;
    s.columns = names(byDay, 'classification', PLACEMENT_NAMES).map((k) => ['classification', k])
      .concat(names(byDay, 'engagement', ENGAGEMENT_NAMES).map((k) => ['engagement', k]));

    // One row per segment per report day; one row per run of days with no report
    s.rows = [];
    let prev = null;
    for (const d of days) {
      if (prev !== null && between(prev, d) > 1) s.rows.push({ day: addDays(prev, 1), until: addDays(d, -1), segment: null, values: [] });
      for (const seg of byDay.get(d).segments) {
        s.rows.push({ day: d, until: d, segment: seg.path.map((p) => p || '(blank)').join(' / ') || 'all mail', values: s.columns.map(([a, k]) => (k in seg[a] ? seg[a][k] : null)) });
      }
      prev = d;
    }

    // [day offset from first, sum across segments] for each report day
    s.trend = (col) => {
      const [a, k] = s.columns[col];
      return days.map((d) => [between(s.first, d), byDay.get(d).segments.reduce((t, seg) => t + (seg[a][k] || 0), 0)]);
    };

    // Day-to-day moves big enough to look at, between one report day and the next
    const changes = [];
    s.columns.forEach(([, k], col) => {
      const rule = CHANGES[k];
      if (!rule) return;
      const pts = s.trend(col);
      for (let i = 1; i < pts.length; i++) {
        const [x0, old] = pts[i - 1];
        const [x1, now] = pts[i];
        // Comcast's buckets are 100 wide, so a move under two buckets is noise
        let verb;
        if (rule === 'rise' && now >= 2 * old && now - old >= MIN_MOVE) verb = 'rose';
        else if (rule === 'fall' && 2 * now <= old && old - now >= MIN_MOVE) verb = 'fell';
        else continue;
        changes.push({ text: k + ' ' + verb + ' from ' + fmt(old) + ' on ' + addDays(s.first, x0) + ' to ' + fmt(now) + ' on ' + addDays(s.first, x1) + '.', day: addDays(s.first, x1), prev: addDays(s.first, x0), from: old, to: now, name: k, verb, col });
      }
    });
    s.changeList = changes.sort((a, b) => cmp(a.day, b.day));
    s.changes = s.changeList.map((c) => c.text);

    const notes = [];
    let pv = null;
    for (const d of days) {
      const r = byDay.get(d);
      if (pv !== null && r.version !== pv) notes.push('Report version changed from ' + pv + ' to ' + r.version + ' on ' + d + ': the provider may have changed what a category counts.');
      pv = r.version;
    }
    // "TBD" is the draft samples' placeholder
    const extra = [...new Set(days.map((d) => byDay.get(d).extra_info))].filter((t) => t !== '' && t !== 'TBD').sort(cmp);
    for (const t of extra) notes.push('Provider note: ' + t);
    const sdi = [...new Set(days.map((d) => byDay.get(d).sdi_used))].filter(usesSdi).sort(cmp);
    if (sdi.length) notes.push('SDI used: ' + sdi.join(', '));
    s.notes = notes;
    return s;
  }

  function streams(reports) {
    const groups = new Map();
    for (const r of reports) {
      const k = JSON.stringify(streamKey(r));
      if (!groups.has(k)) groups.set(k, new Map());
      groups.get(k).set(r.day, r);
    }
    return [...groups.keys()].sort((a, b) => {
      const x = JSON.parse(a); const y = JSON.parse(b);
      return cmp(x[0], y[0]) || cmp(x[1], y[1]) || cmp(x[2], y[2]);
    }).map((k) => { const [src, dom, sel] = JSON.parse(k); return stream(src, dom, sel, groups.get(k)); });
  }

  // [warning, times, first where, first detail], in the order first seen
  function grouped(warnings) {
    const out = new Map();
    for (const [w, d, where] of warnings) {
      if (out.has(w)) out.get(w)[1]++;
      else out.set(w, [w, 1, where, d]);
    }
    return [...out.values()];
  }
  const copiesLine = (k) => k + ' file' + (k !== 1 ? 's' : '') + ' repeated a report already read, such as the same report saved as .json and as .eml, and counted once.';
  const WARNINGS_NOTE = 'Read anyway. Each names the draft section it departs from, or the case the draft leaves open.';
  const warningText = ([w, times, where, detail]) => w + '. ' + times + ' time' + (times === 1 ? '' : 's') + ', first in ' + where + (detail ? ': ' + detail : '');

  function toDict(loaded) {
    return {
      reports: loaded.reports.length,
      streams: streams(loaded.reports).map((s) => ({
        source: s.source, domain: s.domain, selector: s.selector,
        first: s.first, last: s.last, missing_days: s.missing,
        columns: s.columns.map(([, k]) => k),
        days: s.days.map((d) => {
          const r = s.byDay.get(d);
          return { day: d, version: r.version, segments: r.segments.map((g) => ({ segment: g.path, classification: g.classification, engagement: g.engagement })) };
        }),
        changes: s.changes, notes: s.notes,
      })),
      skipped: loaded.skipped.map(([where, reason]) => ({ where, reason })),
      warnings: grouped(loaded.warnings).map(([warning, times, first, detail]) => ({ warning, times, first, detail })),
      copies: loaded.copies,
    };
  }

  // ---- render.py (Markdown; the page draws its own view) ----

  const n = (v) => (v === null ? '' : fmt(v));
  const md = (t) => t.replace(/\\/g, '\\\\').replace(/\|/g, '\\|').replace(/\n/g, ' ');
  const plural = (k, word) => k + ' ' + word + (k === 1 ? '' : 's');
  const countsLine = (loaded, ss) => plural(loaded.reports.length, 'report') + ' in ' + plural(ss.length, 'stream') + '.';
  const span = (s) => (s.first === s.last ? s.first : s.first + ' to ' + s.last) + ': ' + plural(s.days.length, 'report') + (s.missing ? ', ' + plural(s.missing, 'day') + ' with no report' : '');
  const rowDay = (row) => (row.until === row.day ? row.day : row.day + ' to ' + row.until);

  function toMarkdown(loaded, version) {
    const ss = streams(loaded.reports);
    const lines = ['# APRF placement summary', '', countsLine(loaded, ss) + ' ' + CAVEAT, ''];
    for (const s of ss) {
      lines.push('## ' + md(s.source) + ': ' + md(s.domain) + ', selector ' + md(s.selector), '', span(s), '');
      const cols = s.columns.map(([, k]) => k);
      if (!cols.length) { lines.push(NO_COUNTS, ''); continue; }
      lines.push('| Day | Segment | ' + cols.map(md).join(' | ') + ' |');
      lines.push('|---|---|' + '---:|'.repeat(cols.length));
      for (const row of s.rows) {
        if (row.segment === null) lines.push('| ' + rowDay(row) + ' | no report |' + ' |'.repeat(cols.length));
        else lines.push('| ' + row.day + ' | ' + md(row.segment) + ' | ' + row.values.map(n).join(' | ') + ' |');
      }
      lines.push('');
      if (s.changes.length) lines.push('Changes worth a look:', '', ...s.changes.map((c) => '- ' + md(c)), '');
      if (s.notes.length) lines.push(...s.notes.map((t) => '- ' + md(t)), '');
    }
    if (loaded.warnings.length) lines.push('## Warnings', '', WARNINGS_NOTE, '', ...grouped(loaded.warnings).map((g) => '- ' + md(warningText(g))), '');
    if (loaded.skipped.length) lines.push('## Skipped', '', ...loaded.skipped.map(([w, r]) => '- ' + md(w) + ': ' + md(r)), '');
    if (loaded.copies) lines.push(copiesLine(loaded.copies), '');
    lines.push('Read by aprf-reader ' + version + ' against ' + DRAFT + '.');
    return lines.join('\n') + '\n';
  }

  // Line over [day offset, value] points, broken where days are missing
  function spark(points) {
    if (points.length < 2) return '';
    const w = 96; const h = 20;
    const top = Math.max(...points.map((p) => p[1])) || 1;
    const step = w / points[points.length - 1][0];
    const path = [];
    let prev = null;
    for (const [x, v] of points) {
      path.push((prev !== null && x - prev === 1 ? 'L' : 'M') + (x * step).toFixed(1) + ',' + (h - 1 - (v / top) * (h - 2)).toFixed(1));
      prev = x;
    }
    return '<svg class="spark" viewBox="-1 0 ' + (w + 2) + ' ' + h + '" width="' + w + '" height="' + h + '" aria-hidden="true"><path d="' + path.join(' ') +
      '" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
  }

  // Python's html.escape
  const esc = (t) => t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#x27;');

  const CSS = `
:root{--bg:#fff;--fg:#1a1a1a;--muted:#5f6368;--line:#dadce0;--gap:#f6f6f6;--accent:#1a5fb4}
@media (prefers-color-scheme:dark){:root{--bg:#161616;--fg:#e8e8e8;--muted:#a0a0a0;--line:#3a3a3a;--gap:#202020;--accent:#78aeed}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:960px;padding:24px 16px}
h1{font-size:1.5rem}h2{font-size:1.15rem;margin-top:2rem}
.muted{color:var(--muted)}
.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:4px 8px;text-align:right;white-space:nowrap}
th:nth-child(-n+2),td:nth-child(-n+2){text-align:left}
tr.gap td{background:var(--gap);color:var(--muted)}tr.trend td{border-bottom:2px solid var(--line)}
.spark{color:var(--accent);vertical-align:middle}
`;

  // The same page as aprf-reader --html
  function toHtml(loaded, version) {
    const ss = streams(loaded.reports);
    const parts = [
      '<!doctype html><html lang=en><head><meta charset=utf-8>',
      '<meta name=viewport content="width=device-width,initial-scale=1">',
      '<title>APRF placement summary</title><style>' + CSS + '</style></head><body>',
      '<h1>APRF placement summary</h1>',
      '<p>' + countsLine(loaded, ss) + '</p><p class=muted>' + esc(CAVEAT) + '</p>',
    ];
    for (const s of ss) {
      const cols = s.columns.map(([, k]) => k);
      parts.push('<h2>' + esc(s.source) + ': ' + esc(s.domain) + ', selector ' + esc(s.selector) + '</h2>');
      if (!cols.length) { parts.push('<p class=muted>' + esc(span(s)) + '</p><p>' + NO_COUNTS + '</p>'); continue; }
      parts.push('<p class=muted>' + esc(span(s)) + '</p><div class=wrap><table><thead><tr><th>Day<th>Segment');
      parts.push(cols.map((c) => '<th>' + esc(c)).join('') + '</thead><tbody>');
      parts.push('<tr class=trend><td>Trend<td class=muted>sum per day');
      parts.push(cols.map((_, i) => '<td>' + spark(s.trend(i))).join(''));
      for (const row of s.rows) {
        if (row.segment === null) parts.push('<tr class=gap><td>' + rowDay(row) + '<td>no report' + '<td>'.repeat(cols.length));
        else parts.push('<tr><td>' + row.day + '<td>' + esc(row.segment) + row.values.map((v) => '<td>' + n(v)).join(''));
      }
      parts.push('</tbody></table></div>');
      if (s.changes.length) parts.push('<p>Changes worth a look:</p><ul>' + s.changes.map((c) => '<li>' + esc(c)).join('') + '</ul>');
      if (s.notes.length) parts.push('<ul>' + s.notes.map((t) => '<li>' + esc(t)).join('') + '</ul>');
    }
    if (loaded.warnings.length) parts.push('<h2>Warnings</h2><p class=muted>' + WARNINGS_NOTE + '</p><ul>' + grouped(loaded.warnings).map((g) => '<li>' + esc(warningText(g))).join('') + '</ul>');
    if (loaded.skipped.length) parts.push('<h2>Skipped</h2><ul>' + loaded.skipped.map(([w, r]) => '<li>' + esc(w) + ': ' + esc(r)).join('') + '</ul>');
    if (loaded.copies) parts.push('<p class=muted>' + esc(copiesLine(loaded.copies)) + '</p>');
    parts.push('<p class=muted>Read by aprf-reader ' + esc(version) + ' against ' + DRAFT + '. Nothing left this computer.</p>');
    parts.push('</body></html>');
    return parts.join('\n') + '\n';
  }

  const APRF = { DRAFT, CAVEAT, NO_COUNTS, WARNINGS_NOTE, grouped, warningText, copiesLine, ReportError, parse, load, wanted, streams, toDict, toMarkdown, toHtml, span, rowDay, plural, fmt, addDays, dayOffset: between };
  root.APRF = APRF;
  if (typeof module !== 'undefined' && module.exports) module.exports = APRF;
})(typeof window !== 'undefined' ? window : globalThis);
