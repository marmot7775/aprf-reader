// aprf.js: the report format, file reading, and the summary, for the browser.
// A port of src/aprf_reader (report.py, load.py, summary.py, render.py).
// tests/test_web_parity.py runs both on the same files and requires the same
// JSON and Markdown, so the page and the command line cannot drift apart.
'use strict';
(function (root) {
  const DRAFT = 'draft-ietf-mailmaint-aprf-01';
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
  // A move must be at least two of the stream's steps, and never needs to be more than 200:
  // Comcast counts in steps of 100. Section 6 leaves bucketing and bucket size to each provider.
  const MIN_MOVE = 200;
  // Why a stream with no changes was not checked for any (summary.py Stream.not_compared)
  const ONE_REPORT = 'One report day so far, so nothing to compare.';
  const NOTHING_COMPARED = 'Nothing to compare: no two reports in a row carry an inbox, unwanted or negative count.';
  const SUFFIXES = ['.json', '.json.gz', '.eml', '.mbox', '.zip'];
  // A zip of a year of daily reports for a few selectors is a few thousand files
  const MAX_ZIP_ENTRIES = 20000;
  // A daily report is a few KB; this stops a stray huge file or gzip bomb
  const MAX_BYTES = 10 * 1024 * 1024;
  const DAY_MS = 86400000;
  const NO_COUNTS = 'These reports carry no counts.';
  // The row for a report day whose body has no segments
  const NO_COUNTS_ROW = 'no counts';
  const CAVEAT = "Providers may round counts into buckets of a size they choose (Comcast's are steps of 100). " +
    "A bucketed count is the bucket's upper bound: a ceiling, not an exact figure. " +
    'Placement counts mail received that day; engagement counts actions taken that day on mail ' +
    'from any day. Dividing one count by another gives a ratio nobody measured.';

  class ReportError extends Error {}
  // A report nests about four levels deep. Deeper than this is refused (report.py MAX_DEPTH), so a
  // hostile value cannot exhaust the stack, and the rest of the file is still read.
  const MAX_DEPTH = 32;
  const TOO_DEEP = 'JSON nested too deeply';
  function tooDeep(doc) {
    const stack = [[doc, 1]];
    while (stack.length) {
      const [v, depth] = stack.pop();
      if (v !== null && typeof v === 'object') {
        if (depth > MAX_DEPTH) return true;
        for (const c of Array.isArray(v) ? v : Object.values(v)) stack.push([c, depth + 1]);
      }
    }
    return false;
  }
  // Placement names section 6 gives as examples; engagement names section 5.2.2 lists
  const NAMED_CLASSIFICATION = ['inbox', 'unwanted', 'forwarded', 'promotional'];
  // sdi as published in DNS (section 4): a header field name, a comma, one separator character.
  // Section 4.1: the separator MUST NOT be any of ";=,". The field name may hold ";" itself.
  const SDI_FORMAT = /^[!-9;-~]+,[^\s;=,]$/;
  const ONE_DAY = 'not one UTC day from 00:00:00 to 23:59:59, which draft section 5.1 requires';

  // ---- report.py ----

  const isInt = (n) => typeof n === 'number' && Number.isInteger(n);
  // Header text as report.py writes it: a string as is, a whole number without a point (1.0 is
  // "1"), another plain number as both languages print it; null for a list, an object, true,
  // and a number the two would print differently
  function text(v) {
    if (typeof v === 'string') return v;
    if (typeof v !== 'number' || !isFinite(v)) return null;
    if (Number.isInteger(v)) return Math.abs(v) <= MAX_COUNT ? String(v) : null;
    return Math.abs(v) >= 1e-4 && Math.abs(v) < 1e15 ? String(v) : null;
  }
  // "" when absent or null, an error when present as a list, object or true
  function headerText(h, name) {
    if (h[name] === null || h[name] === undefined) return '';
    const t = text(h[name]);
    if (t === null) throw new ReportError('header.' + name + ' is not text or a number (section 5.2.1)');
    return t;
  }

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
    // No prototype, so a count named constructor or __proto__ is a count like any other
    const out = Object.create(null);
    for (const [name, n] of Object.entries(value)) {
      if (!isInt(n) || n < 0 || n > MAX_COUNT) throw new ReportError(where + '.' + name + ' is not a count (' + section + '): ' + JSON.stringify(n));
      // -0 is zero: Python has no negative zero, and toLocaleString would print "-0"
      out[name] = n === 0 ? 0 : n;
    }
    return out;
  }

  function segment(item, i) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) throw new ReportError('body[' + i + '] is not an object (section 5.2.2)');
    const seg = item.segment;
    let path;
    // Section 5.2.2 says a list of strings; a lone string is read as one item, with a warning
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
    const source = headerText(h, 'source');
    const version = headerText(h, 'version');
    return {
      source: source || 'unknown',
      dkim_domain: h.dkim_domain,
      dkim_selector: h.dkim_selector,
      // + 0 turns -0 into 0, as Python reads it
      report_start: h.report_start + 0,
      report_end: h.report_end + 0,
      version,
      // Comcast's beta sends " - N/A - Beta - Not in use"
      sdi_used: (text(h.sdi_used) || '').replace(/^[ -]+|[ -]+$/g, ''),
      contact_info: text(h.contact_info) || '',
      extra_info: text(h.extra_info) || '',
      segments,
      warnings: reportWarnings(h, segments).concat(body.flatMap((s, i) => (s && typeof s === 'object' && typeof s.segment === 'string'
        ? [['segment is a string; section 5.2.2 says a list of strings. Read as a one-item list', 'body[' + i + ']']] : []))),
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
  // Section 5.2.1 requires source and version; a report without one is still read, with a warning
  const MISSING_HEADER = {
    source: 'header.source is missing (section 5.2.1); read, with the provider shown as unknown',
    version: 'header.version is missing (section 5.2.1); read',
  };
  function reportWarnings(h, segments) {
    const out = [];
    for (const name of ['source', 'version']) {
      if (h[name] === null || h[name] === undefined) out.push([MISSING_HEADER[name], '']);
    }
    if (!('sdi_used' in h)) out.push(['no sdi_used field; section 5.2.1 requires N/A when no SDI is used', '']);
    else {
      const raw = h.sdi_used;
      if (!(raw === 'N/A' || raw === 'N/F' || (typeof raw === 'string' && SDI_FORMAT.test(raw)))) {
        out.push(['sdi_used is not N/A, N/F, or the sdi value as published in DNS (section 5.2.1)', JSON.stringify(raw)]);
      }
    }
    for (const name of ['contact_info', 'extra_info']) {
      if (h[name] !== null && h[name] !== undefined && text(h[name]) === null) out.push(['header.' + name + ' is not text or a number (section 5.2.1); ignored', '']);
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

  // A backslash escape in JSON text. A \uD800 to \uDFFF escape that is not half of a pair is a
  // lone surrogate: valid JSON, but no character. Both readers read it as U+FFFD (report.py
  // _no_lone_surrogates), so sorting and output agree. Each backslash takes the character after
  // it, so an escaped backslash is never read as the start of an escape.
  const ESCAPE = /\\(?:u[dD][89abAB][0-9a-fA-F]{2}\\u[dD][c-fC-F][0-9a-fA-F]{2}|(u[dD][89a-fA-F][0-9a-fA-F]{2})|[\s\S])/g;
  const noLoneSurrogates = (t) => (/\\u[dD][89a-fA-F]/.test(t) ? t.replace(ESCAPE, (m, lone) => (lone ? '\\ufffd' : m)) : t);

  // Each report in a document, or the reason it cannot be read, so one bad
  // report does not cost the rest of the file
  function parseItems(bytes) {
    let doc;
    try {
      doc = JSON.parse(noLoneSurrogates(new TextDecoder('utf-8', { fatal: true }).decode(bytes)));
    } catch (e) {
      throw new ReportError(e instanceof RangeError ? TOO_DEEP : 'not valid JSON, which section 5 requires: ' + e.message);
    }
    const items = Array.isArray(doc) ? doc : [doc];
    if (!items.length) throw new ReportError('empty list');
    return items.map((o) => {
      if (tooDeep(o)) return new ReportError(TOO_DEEP);
      try { return report(o); } catch (e) {
        if (e instanceof ReportError) return e;
        // The stack ran out on a deeply nested value: this report is refused, the rest of the file is not
        if (e instanceof RangeError) return new ReportError(TOO_DEEP);
        throw e;
      }
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
  function bytesOf(s) {
    const out = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i) & 255;
    return out;
  }
  const isGzip = (b) => b.length > 1 && b[0] === 0x1f && b[1] === 0x8b;

  async function gunzip(bytes, format) {
    const reader = new Blob([bytes]).stream().pipeThrough(new DecompressionStream(format || 'gzip')).getReader();
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

  // Header parameters. RFC 2231: name*=charset'lang'%XX is percent-encoded, and a long value
  // may be split into name*0, name*1 (each "name*N*" when encoded), joined in numeric order.
  function params(value) {
    const out = Object.create(null);
    const parts = Object.create(null);
    const re = /;\s*([\w.*-]+)\s*=\s*("(?:[^"\\]|\\.)*"|[^;\s]*)/g;
    let m;
    while ((m = re.exec(value))) {
      let v = m[2];
      if (v.startsWith('"')) v = v.slice(1, -1).replace(/\\(.)/g, '$1');
      const k = m[1].toLowerCase();
      const cont = /^(.+?)\*(\d+)(\*?)$/.exec(k);
      if (cont) {
        (parts[cont[1]] = parts[cont[1]] || []).push({ n: Number(cont[2]), v, encoded: cont[3] === '*' });
        continue;
      }
      if (k.endsWith('*')) {
        const name = k.slice(0, -1);
        try { v = decodeURIComponent(v.replace(/^[^']*'[^']*'/, '')); } catch (e) { /* keep raw */ }
        out[name] = v;
      } else out[k] = v;
    }
    for (const [name, list] of Object.entries(parts)) {
      if (name in out) continue;
      list.sort((x, y) => x.n - y.n);
      // Encoded pieces are decoded together, so a character split across pieces survives
      let joined = '';
      let pending = '';
      const flush = () => {
        try { joined += decodeURIComponent(pending); } catch (e) { joined += pending; }
        pending = '';
      };
      list.forEach((p, i) => {
        if (p.encoded) pending += i === 0 ? p.v.replace(/^[^']*'[^']*'/, '') : p.v;
        else { flush(); joined += p.v; }
      });
      flush();
      out[name] = joined;
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

  // ---- zip, as Python's zipfile reads it ----

  let CRC_TABLE = null;
  function crc32(b) {
    if (!CRC_TABLE) {
      CRC_TABLE = new Uint32Array(256);
      for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; CRC_TABLE[n] = c >>> 0; }
    }
    let c = 0xffffffff;
    for (let i = 0; i < b.length; i++) c = CRC_TABLE[(c ^ b[i]) & 255] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  }

  // The central directory: [{ name, flags, method, crc, csize, usize, offset }]
  function zipEntries(b) {
    const v = new DataView(b.buffer, b.byteOffset, b.byteLength);
    let end = -1;
    for (let i = b.length - 22; i >= Math.max(0, b.length - 22 - 65535); i--) if (v.getUint32(i, true) === 0x06054b50) { end = i; break; }
    if (end < 0) throw new ReportError('not a readable zip file');
    const count = v.getUint16(end + 10, true);
    let at = v.getUint32(end + 16, true);
    const out = [];
    for (let k = 0; k < count; k++) {
      if (at + 46 > b.length || v.getUint32(at, true) !== 0x02014b50) throw new ReportError('not a readable zip file');
      const flags = v.getUint16(at + 8, true);
      const nlen = v.getUint16(at + 28, true);
      const raw = b.subarray(at + 46, at + 46 + nlen);
      // shortcut: names without the UTF-8 flag are read as Latin-1, where zipfile uses cp437; both agree on ASCII
      const name = flags & 0x800 ? new TextDecoder().decode(raw) : latin1(raw);
      out.push({ name, flags, method: v.getUint16(at + 10, true), crc: v.getUint32(at + 16, true), csize: v.getUint32(at + 20, true), usize: v.getUint32(at + 24, true), offset: v.getUint32(at + 42, true) });
      at += 46 + nlen + v.getUint16(at + 30, true) + v.getUint16(at + 32, true);
    }
    return out;
  }

  async function unzipEntry(b, e) {
    const v = new DataView(b.buffer, b.byteOffset, b.byteLength);
    if (e.offset + 30 > b.length || v.getUint32(e.offset, true) !== 0x04034b50) throw new ReportError('bad entry');
    const start = e.offset + 30 + v.getUint16(e.offset + 26, true) + v.getUint16(e.offset + 28, true);
    const comp = b.subarray(start, start + e.csize);
    if (comp.length !== e.csize) throw new ReportError('bad entry');
    let data;
    if (e.method === 0) data = comp;
    else if (e.method === 8) data = await gunzip(comp, 'deflate-raw');
    else throw new ReportError('bad entry');
    if (data.length !== e.usize || crc32(data) !== e.crc) throw new ReportError('bad entry');
    return data;
  }

  // Each report file in a zip, in name order, as { skip: [label, reason] } or the result of
  // use(label, bytes); members unpack in parallel, and a bad one is skipped, not fatal
  async function zipFiles(name, bytes, use) {
    // __MACOSX holds Finder metadata copies named like the real files
    const members = zipEntries(bytes).filter((e) => !e.name.endsWith('/') && !e.name.startsWith('__MACOSX/') && SUFFIXES.some((x) => e.name.toLowerCase().endsWith(x)))
      .sort((a, b) => cmp(a.name, b.name));
    if (members.length > MAX_ZIP_ENTRIES) throw new ReportError('more than ' + fmt(MAX_ZIP_ENTRIES) + ' report files in one zip');
    return Promise.all(members.map(async (e) => {
      const label = name + '/' + e.name;
      const lower = e.name.toLowerCase();
      if (lower.endsWith('.zip') || lower.endsWith('.mbox')) return { skip: [label, 'a zip or mailbox inside a zip is not read; unzip it first'] };
      if (e.flags & 1) return { skip: [label, 'encrypted in the zip'] };
      if (e.usize > MAX_BYTES) return { skip: [label, 'larger than 10 MB'] };
      let data;
      try { data = await unzipEntry(bytes, e); } catch (err) {
        if (!(err instanceof ReportError)) throw err;
        return { skip: [label, 'could not be unpacked from the zip'] };
      }
      return use(label, data);
    }));
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

  const DUPLICATE_DAY = 'the same day came twice from one provider; the later copy replaced the earlier one, ' +
    'as section 8.2 says. Later means read later: arrival order in a mailbox, name order for files';
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
  // What stands in for a rolled-up selector (header "*") or domain (header "*.example.org") in
  // the name and subject is unsettled in -01: "!" and an empty field have been proposed on the
  // list. Accept those and "*" until -02 decides; revisit then. Thread on the -01 I-D Action:
  // https://mailarchive.ietf.org/arch/msg/mailmaint/qA9kIVnK5-9w1uvL48x6wleAUMk/
  const ROLLUP_STANDINS = ['!', '', '*'];
  // Section 8 bases for a report, the header's own spelling first
  function reportNames(r) {
    const sels = [r.dkim_selector].concat(r.dkim_selector === '*' ? ROLLUP_STANDINS : []);
    const doms = [r.dkim_domain].concat(r.dkim_domain.startsWith('*.') ? ROLLUP_STANDINS : []);
    const src = r.source.replace(/ /g, '');
    return [...new Set(doms.flatMap((d) => sels.map((s) => r.day.replace(/-/g, '') + '_' + d + '_' + s + '_' + src)))];
  }

  function mailWarnings(mail, items) {
    const out = [];
    if (mail.msgCtype !== 'multipart/report') out.push(['message Content-Type is not multipart/report (section 8)', mail.msgCtype]);
    const want = mail.gz ? 'application/gzip' : 'application/json';
    if (mail.ctype !== want) out.push(['attachment Content-Type is not application/json, or application/gzip when compressed (sections 8 and 8.1)', mail.ctype]);
    if (items.length > 1) out.push(['one attachment holds several reports; the section 8 name and subject fit one', items.length + ' reports']);
    else if (!(items[0] instanceof ReportError)) {
      const bases = reportNames(items[0]);
      const base = bases[0];
      const ext = mail.gz ? '.json.gz' : '.json';
      const file = base + ext;
      if (bases.some((b) => fold(mail.name) === fold(b + ext))) { /* matches */ } else if (bases.some((b) => loose(b + ext, mail.name))) out.push([NAME_UNSAFE, mail.name + '; section 8 gives ' + file]);
      else out.push(['attachment name does not follow section 8: <yyyymmdd>_<DKIM domain>_<DKIM selector>_<source>.json', (mail.name || '(none)') + '; expected ' + file]);
      const subj = 'APRF: ' + base;
      if (bases.some((b) => fold(mail.subject) === fold('APRF: ' + b))) { /* matches */ } else if (bases.some((b) => loose('APRF: ' + b, mail.subject))) out.push([SUBJECT_UNSAFE, mail.subject + '; section 8 gives ' + subj]);
      else out.push(['subject does not follow section 8: APRF: <yyyymmdd>_<DKIM domain>_<DKIM selector>_<source>', (mail.subject || '(none)') + '; expected ' + subj]);
    }
    return out;
  }

  async function sha256(bytes) {
    const subtle = (root.crypto && root.crypto.subtle) || require('crypto').webcrypto.subtle;
    return latin1(new Uint8Array(await subtle.digest('SHA-256', bytes)));
  }

  // Files are read in name order: by full path, one UTF-16 code unit at a time (what < compares),
  // which load.py name_order matches. Pass the files to load() through this.
  const sortFiles = (files) => files.slice().sort((a, b) => cmp(a.name, b.name));

  // files: [{ name, bytes }] in the order to read them. Returns { reports, skipped }.
  async function load(files) {
    // warnings: [warning, detail, where]; copies: files whose report bytes were already read
    const out = { reports: [], skipped: [], warnings: [], copies: 0 };
    const parsed = new Map();
    const seenKeys = new Map();

    // Whether reading these reports again would change nothing: for each day they hold, the
    // copy kept is still the last one they give for that day (load.py current)
    function current(items) {
      const last = new Map();
      for (const r of items) if (!(r instanceof ReportError)) last.set(keyOf(r), r);
      return [...last].every(([k, r]) => seenKeys.has(k) && seenKeys.get(k)[1] === r);
    }

    function add(where, data, mail, digest) {
      const seen = parsed.has(digest);
      if (!seen) {
        try { parsed.set(digest, parseItems(data)); } catch (e) {
          if (!(e instanceof ReportError)) throw e;
          parsed.set(digest, e);
        }
      }
      const items = parsed.get(digest);
      // A file read before is a copy only while what it holds is what is kept. After a changed
      // copy of the same day (A, then B, then A again), A is the later copy and replaces B.
      const copy = seen && (items instanceof ReportError || current(items));
      // An email is checked even when its report was already read from another file
      if (mail && !(items instanceof ReportError)) for (const [w, d] of mailWarnings(mail, items)) out.warnings.push([w, d, where]);
      if (copy) { out.copies++; return; }
      if (items instanceof ReportError) { out.skipped.push([where, items.message]); return; }
      items.forEach((r, i) => {
        const label = items.length > 1 ? where + ' report ' + (i + 1) : where;
        if (r instanceof ReportError) { out.skipped.push([label, r.message]); return; }
        for (const [w, d] of r.warnings) out.warnings.push([w, d, label]);
        // Section 8.2: a later report for the same name and day overwrites the earlier.
        // version marks a difference, not an order, so read order decides.
        const k = keyOf(r);
        if (seenKeys.has(k)) {
          const [firstLabel, first] = seenKeys.get(k);
          out.warnings.push([DUPLICATE_DAY, firstLabel + ' replaced' + differs(r, first), label]);
          out.reports[out.reports.indexOf(first)] = r;
        } else out.reports.push(r);
        seenKeys.set(k, [label, r]);
      });
    }

    // [label, report JSON bytes, how it arrived] for each report in one file's bytes
    async function payloadsOf(name, bytes) {
      if (bytes.length > MAX_BYTES) throw new ReportError('larger than 10 MB');
      if (name.toLowerCase().endsWith('.eml')) {
        const found = await attachments(latin1(bytes), name);
        if (!found.length) throw new ReportError('no report attachment, which section 8 calls for');
        return found;
      }
      return isGzip(bytes) ? [[name, await gunzip(bytes), null]] : [[name, bytes, null]];
    }

    // Unpacking and hashing are independent per file, so they run in parallel; the results
    // are then applied in file order, so the summary never depends on which finished first
    async function prepare(label, bytes) {
      try {
        const payloads = await payloadsOf(label, bytes);
        return { label, payloads: await Promise.all(payloads.map(async ([w, d, m]) => [w, d, m, await sha256(d)])) };
      } catch (e) {
        if (!(e instanceof ReportError)) throw e;
        return { skip: [label, e.message] };
      }
    }
    const apply = (r) => {
      if (r.skip) out.skipped.push(r.skip);
      else for (const [w, d, m, digest] of r.payloads) add(w, d, m, digest);
    };
    const ready = await Promise.all(files.map(async ({ name, bytes }) => {
      if (isMbox(name)) return null;
      if (!baseName(name).endsWith('.zip')) return prepare(name, bytes);
      try { return { zip: await zipFiles(name, bytes, prepare) }; } catch (e) {
        if (!(e instanceof ReportError)) throw e;
        return { skip: [name, e.message] };
      }
    }));

    for (let fi = 0; fi < files.length; fi++) {
      const { name, bytes } = files[fi];
      const r = ready[fi];
      if (r) { if (r.zip) r.zip.forEach(apply); else apply(r); continue; }
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
            for (const [w, d, m] of found) add(w, d, m, await sha256(d));
          }
          if (without) out.skipped.push([name, without + ' message' + (without === 1 ? '' : 's') + ' with no report attached']);
          continue;
        }
      } catch (e) {
        if (!(e instanceof ReportError)) throw e;
        out.skipped.push([name, e.message]);
      }
    }
    return out;
  }

  // A name for pasted text, so it is read like a file: a report's JSON, or a whole
  // email such as Gmail's Show original, attachment and all (load.py pasted_name)
  function pastedName(bytes) {
    let i = 0;
    while (i < bytes.length && [9, 10, 13, 32].includes(bytes[i])) i++;
    if (bytes[i] === 0x5b || bytes[i] === 0x7b) return 'pasted.json';
    return isGzip(bytes) ? 'pasted.json.gz' : 'pasted.eml';
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

  const gcd = (a, b) => { while (b) [a, b] = [b, a % b]; return a; };

  // domain and selector as the stream's first report writes them; domainKey is the form it is
  // grouped by (summary.py Stream)
  function stream(source, domainKey, byDay) {
    const days = [...byDay.keys()].sort(cmp);
    const head = byDay.get(days[0]);
    const s = { source, domain: head.dkim_domain, domainKey, selector: head.dkim_selector, byDay, days, first: days[0], last: days[days.length - 1] };
    // The domain as written, with the form it is grouped by when that looks different
    s.heading = s.domain.toLowerCase() === domainKey ? s.domain : s.domain + ' (' + domainKey + ')';
    s.missing = between(s.first, s.last) + 1 - days.length;
    s.columns = names(byDay, 'classification', PLACEMENT_NAMES).map((k) => ['classification', k])
      .concat(names(byDay, 'engagement', ENGAGEMENT_NAMES).map((k) => ['engagement', k]));

    // One row per segment per report day; one row per run of days with no report
    s.rows = [];
    let prev = null;
    for (const d of days) {
      if (prev !== null && between(prev, d) > 1) s.rows.push({ day: addDays(prev, 1), until: addDays(d, -1), segment: null, values: [] });
      const segments = byDay.get(d).segments;
      // A report with no segments is a day the provider reported, with nothing to count
      if (!segments.length) s.rows.push({ day: d, until: d, segment: NO_COUNTS_ROW, values: s.columns.map(() => null) });
      for (const seg of segments) {
        s.rows.push({ day: d, until: d, segment: seg.path.map((p) => p || '(blank)').join(' / ') || 'all mail', values: s.columns.map(([a, k]) => (Object.hasOwn(seg[a], k) ? seg[a][k] : null)) });
      }
      prev = d;
    }

    // [day offset from first, sum across segments] for each report day; null for a day whose
    // segments do not carry the count, which is missing, not zero
    const series = (col) => {
      const [a, k] = s.columns[col];
      return days.map((d) => {
        const have = byDay.get(d).segments.filter((seg) => Object.hasOwn(seg[a], k)).map((seg) => seg[a][k]);
        return [between(s.first, d), have.length ? have.reduce((t, v) => t + v, 0) : null];
      });
    };
    // The series without its missing days; a skipped day leaves a gap in the line
    s.trend = (col) => series(col).filter((p) => p[1] !== null);

    // The step every count is a multiple of: 100 for Comcast's buckets, 1 for exact counts, 0
    // when every count is zero. A move of one step is rounding, so a change is two, up to 200.
    let step = 0;
    for (const r of byDay.values()) for (const g of r.segments) for (const a of ['classification', 'engagement']) for (const v of Object.values(g[a])) step = gcd(step, v);
    const minMove = step ? Math.min(MIN_MOVE, 2 * step) : MIN_MOVE;

    // Why no day could be held against the one before, or null when one was
    s.notCompared = days.length < 2 ? ONE_REPORT : NOTHING_COMPARED;
    s.columns.forEach(([, k], col) => {
      if (!CHANGES[k] || days.length < 2) return;
      const pts = series(col);
      if (pts.some((p, i) => i && p[1] !== null && pts[i - 1][1] !== null)) s.notCompared = null;
    });

    // Day-to-day moves big enough to look at, between one report day and the next
    const changes = [];
    s.columns.forEach(([, k], col) => {
      const rule = CHANGES[k];
      if (!rule) return;
      const pts = series(col);
      for (let i = 1; i < pts.length; i++) {
        const [x0, old] = pts[i - 1];
        const [x1, now] = pts[i];
        // Nothing is computed across a day that does not carry the count
        if (old === null || now === null) continue;
        // A move of one bucket is rounding (minMove)
        let verb;
        if (rule === 'rise' && now >= 2 * old && now - old >= minMove) verb = 'rose';
        else if (rule === 'fall' && 2 * now <= old && old - now >= minMove) verb = 'fell';
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
    }).map((k) => { const [src, dom] = JSON.parse(k); return stream(src, dom, groups.get(k)); });
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
        source: s.source, domain: s.domain, domain_key: s.domainKey, selector: s.selector,
        first: s.first, last: s.last, missing_days: s.missing,
        columns: s.columns.map(([, k]) => k),
        days: s.days.map((d) => {
          const r = s.byDay.get(d);
          return { day: d, version: r.version, segments: r.segments.map((g) => ({ segment: g.path, classification: g.classification, engagement: g.engagement })) };
        }),
        changes: s.changes, not_compared: s.notCompared, notes: s.notes,
      })),
      skipped: loaded.skipped.map(([where, reason]) => ({ where, reason })),
      warnings: grouped(loaded.warnings).map(([warning, times, first, detail]) => ({ warning, times, first, detail })),
      copies: loaded.copies,
    };
  }

  // ---- render.py (Markdown; the page draws its own view) ----

  const n = (v) => (v === null ? '' : fmt(v));
  // CommonMark section 2.1 ends a line at LF, CR or CRLF; U+2028 and U+2029 are line breaks to
  // other readers. Each becomes one space, so provider text cannot split a table row.
  const md = (t) => t.replace(/\\/g, '\\\\').replace(/\|/g, '\\|').replace(/\r\n|[\r\n\u2028\u2029]/g, ' ');
  const plural = (k, word) => k + ' ' + word + (k === 1 ? '' : 's');
  const countsLine = (loaded, ss) => plural(loaded.reports.length, 'report') + ' in ' + plural(ss.length, 'stream') + '.';
  const span = (s) => (s.first === s.last ? s.first : s.first + ' to ' + s.last) + ': ' + plural(s.days.length, 'report') + (s.missing ? ', ' + plural(s.missing, 'day') + ' with no report' : '');
  const rowDay = (row) => (row.until === row.day ? row.day : row.day + ' to ' + row.until);

  function toMarkdown(loaded, version) {
    const ss = streams(loaded.reports);
    const lines = ['# APRF placement summary', '', countsLine(loaded, ss) + ' ' + CAVEAT, ''];
    for (const s of ss) {
      lines.push('## ' + md(s.source) + ': ' + md(s.heading) + ', selector ' + md(s.selector), '', span(s), '');
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
      parts.push('<h2>' + esc(s.source) + ': ' + esc(s.heading) + ', selector ' + esc(s.selector) + '</h2>');
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
    parts.push('<p class=muted>Read by aprf-reader ' + esc(version) + ' against ' + DRAFT + '.</p>');
    parts.push('</body></html>');
    return parts.join('\n') + '\n';
  }

  // A skip reason as a stable code, for the usage note. The reason strings are
  // written for a person reading the page; these codes are written to be counted.
  // A code never carries the filename or the exception text, so a note can say
  // what kind of file was refused without saying anything about whose it was.
  const SKIP_CODES = [
    [/inside a zip is not read/, 'nested_archive'],
    [/^encrypted in the zip$/, 'encrypted'],
    [/^larger than /, 'too_large'],
    [/could not be unpacked/, 'unpack_failed'],
    [/with no report attached$/, 'no_report_attached'],
    [/report files in one zip$/, 'zip_too_many'],
  ];
  // Anything unmatched is 'parse_failed': a parse failure, which is the one skip
  // reason that may mean the reader has a bug rather than the file being wrong.
  function skipCode(reason) {
    const text = typeof reason === 'string' ? reason : '';
    for (const [re, code] of SKIP_CODES) if (re.test(text)) return code;
    return 'parse_failed';
  }

  const APRF = { sortFiles, pastedName, DRAFT, CAVEAT, NO_COUNTS, WARNINGS_NOTE, grouped, warningText, copiesLine, ReportError, parse, load, wanted, streams, toDict, toMarkdown, toHtml, span, rowDay, plural, fmt, addDays, dayOffset: between, skipCode };
  root.APRF = APRF;
  if (typeof module !== 'undefined' && module.exports) module.exports = APRF;
})(typeof window !== 'undefined' ? window : globalThis);
