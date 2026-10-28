// app.js: rendering and file handling only. Reading and summarising live in
// src/aprf.js, which the tests hold to the command line's output.
'use strict';
(function () {
  const APRF = window.APRF;
  const VERSION = window.APRF_VERSION;
  const $ = (id) => document.getElementById(id);
  const SVGNS = 'http://www.w3.org/2000/svg';
  let current = null; // { loaded, name }

  // Elements are built with text nodes only; report text never becomes markup.
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) el.append(kid);
    return el;
  }
  function s(tag, attrs) {
    const el = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
    return el;
  }
  const icon = (d) => { const svg = s('svg', { viewBox: '0 0 16 16', 'aria-hidden': 'true' }); svg.append(s('path', { d, fill: 'none', stroke: 'currentColor', 'stroke-width': '1.8', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' })); return svg; };
  const ICON = {
    look: 'M8 2.5 14.5 13.5h-13zM8 6.5v3M8 11.6v.1',
    ok: 'M3 8.5 6.5 12 13 4.5',
    copy: 'M5.5 5.5h8v8h-8zM10.5 5.5v-3h-8v8h3',
    save: 'M8 2.5v8m0 0-3-3m3 3 3-3M2.5 11v2.5h11V11',
  };

  const DATE = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  const DATE_Y = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
  const when = (day, year) => (year ? DATE_Y : DATE).format(new Date(day + 'T00:00:00Z'));
  const range = (a, b) => (a === b ? when(a, true) : (a.slice(0, 4) === b.slice(0, 4) ? when(a) : when(a, true)) + ' to ' + when(b, true));

  // ---- reading files ----

  function status(text, error) {
    const el = $('status');
    el.textContent = text;
    el.hidden = !text;
    el.classList.toggle('error', !!error);
  }

  async function readAll(entries) {
    // entries: [{ name, file }]; sorted by path so results never depend on drop order
    entries.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
    const files = [];
    for (const { name, file } of entries) files.push({ name, bytes: new Uint8Array(await file.arrayBuffer()) });
    return files;
  }

  async function run(entries, label) {
    if (!entries.length) { status('No report files found there.', true); return; }
    status('Reading ' + APRF.plural(entries.length, 'file') + '...');
    try {
      const loaded = await APRF.load(await readAll(entries));
      if (!loaded.reports.length) {
        status('No APRF reports found in ' + APRF.plural(entries.length, 'file') + '. ' + (loaded.skipped[0] ? 'First problem: ' + loaded.skipped[0][1] + '.' : ''), true);
        return;
      }
      status('');
      current = { loaded, sample: label === 'sample' };
      render();
    } catch (e) {
      status('Something went wrong reading those files: ' + e.message, true);
    }
  }

  // A dropped folder is walked like the command line walks one: report suffixes only
  function walk(entry, prefix, out) {
    return new Promise((resolve) => {
      if (entry.isFile) {
        entry.file((file) => { if (APRF.wanted(entry.name)) out.push({ name: prefix + entry.name, file }); resolve(); }, () => resolve());
      } else if (entry.isDirectory) {
        const reader = entry.createReader();
        const next = () => reader.readEntries(async (list) => {
          if (!list.length) return resolve();
          for (const e of list) await walk(e, prefix + entry.name + '/', out);
          next();
        }, () => resolve());
        next();
      } else resolve();
    });
  }

  async function fromDrop(dt) {
    const out = [];
    const items = [...(dt.items || [])].filter((i) => i.kind === 'file');
    const entries = items.map((i) => (i.webkitGetAsEntry ? i.webkitGetAsEntry() : null));
    if (entries.length && entries.every(Boolean)) {
      for (let i = 0; i < entries.length; i++) {
        // A file dropped on its own is read whatever its name, as when named on the command line
        if (entries[i].isFile) out.push({ name: entries[i].name, file: items[i].getAsFile() });
        else await walk(entries[i], '', out);
      }
    } else {
      for (const file of dt.files) out.push({ name: file.name, file });
    }
    return out;
  }

  // ---- charts ----

  function nice(max) {
    if (max <= 0) return 100;
    const p = Math.pow(10, Math.floor(Math.log10(max)));
    // Steps whose half is also a round number, so the middle gridline reads cleanly
    for (const m of [1, 2, 4, 5, 10]) if (m * p >= max) return m * p;
    return 10 * p;
  }
  const short = (v) => (v >= 10000 ? (v / 1000).toLocaleString('en-US', { maximumFractionDigits: 0 }) + 'k' : v >= 1000 ? (v / 1000).toLocaleString('en-US', { maximumFractionDigits: 1 }) + 'k' : String(v));

  function chart(stream, col) {
    const name = stream.columns[col][1];
    const pts = stream.trend(col);
    const span = Math.max(1, pts[pts.length - 1][0]);
    const W = 300; const H = 120; const L = 34; const R = 8; const T = 8; const B = 20;
    const top = nice(Math.max(...pts.map((p) => p[1])));
    const x = (d) => L + (pts.length === 1 ? (W - L - R) / 2 : (d / span) * (W - L - R));
    const y = (v) => T + (1 - v / top) * (H - T - B);
    const svg = s('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', tabindex: '0' });
    const latest = pts[pts.length - 1][1];
    svg.setAttribute('aria-label', name + ' per day from ' + range(stream.first, stream.last) + '. Latest up to ' + APRF.fmt(latest) + '. Use the arrow keys to read each day.');

    for (const v of [0, top / 2, top]) {
      svg.append(s('line', { class: 'grid', x1: L, x2: W - R, y1: y(v), y2: y(v) }));
      const t = s('text', { class: 'axis', x: L - 6, y: y(v) + 3, 'text-anchor': 'end' });
      t.textContent = short(v);
      svg.append(t);
    }
    // Days with no report: a shaded band, and the line breaks across it
    for (let i = 1; i < pts.length; i++) {
      if (pts[i][0] - pts[i - 1][0] > 1) {
        svg.append(s('rect', { class: 'gapband', x: x(pts[i - 1][0] + 0.5), y: T, width: Math.max(1, x(pts[i][0] - 0.5) - x(pts[i - 1][0] + 0.5)), height: H - T - B }));
      }
    }
    let d = '';
    pts.forEach(([dx, v], i) => { d += (i && dx - pts[i - 1][0] === 1 ? 'L' : 'M') + x(dx).toFixed(1) + ',' + y(v).toFixed(1); });
    if (pts.length === 1 || pts.some((p, i) => i && p[0] - pts[i - 1][0] > 1)) {
      // Lone points (one-day runs) would be invisible as a path
      pts.forEach(([dx, v], i) => {
        const alone = (i === 0 || dx - pts[i - 1][0] > 1) && (i === pts.length - 1 || pts[i + 1][0] - dx > 1);
        if (alone) svg.append(s('circle', { class: 'dot', cx: x(dx), cy: y(v), r: 3.5 }));
      });
    }
    svg.append(s('path', { class: 'line', d }));
    for (const c of stream.changeList.filter((c) => c.col === col)) {
      const dx = APRF.dayOffset(stream.first, c.day);
      const v = pts.find((p) => p[0] === dx)[1];
      const ring = s('circle', { class: 'flag', cx: x(dx), cy: y(v), r: 5 });
      svg.append(ring);
    }
    for (const [label, anchor, at] of [[when(stream.first), 'start', L], [when(stream.last), 'end', W - R]]) {
      if (stream.first === stream.last && anchor === 'end') continue;
      const t = s('text', { class: 'axis', x: at, y: H - 5, 'text-anchor': stream.first === stream.last ? 'middle' : anchor });
      if (stream.first === stream.last) t.setAttribute('x', W / 2);
      t.textContent = label;
      svg.append(t);
    }

    // Hover and keys: a crosshair on the nearest day, values in a tip
    const cross = s('line', { class: 'cross', y1: T, y2: H - B, visibility: 'hidden' });
    const dot = s('circle', { class: 'dot', r: 4, visibility: 'hidden' });
    svg.append(cross, dot);
    const tip = h('div', { class: 'tip', hidden: true });
    let at = null;
    function show(dx) {
      at = Math.max(0, Math.min(span, dx));
      const p = pts.find((q) => q[0] === at);
      const day = APRF.addDays(stream.first, at);
      cross.setAttribute('x1', x(at)); cross.setAttribute('x2', x(at)); cross.setAttribute('visibility', 'visible');
      if (p) { dot.setAttribute('cx', x(at)); dot.setAttribute('cy', y(p[1])); dot.setAttribute('visibility', 'visible'); } else dot.setAttribute('visibility', 'hidden');
      tip.replaceChildren(h('b', {}, p ? 'up to ' + APRF.fmt(p[1]) : 'no report'), h('span', {}, when(day, true)));
      tip.hidden = false;
      const box = svg.getBoundingClientRect();
      const host = svg.parentElement.getBoundingClientRect();
      const px = box.left - host.left + (x(at) / W) * box.width;
      tip.style.top = (box.top - host.top - 8) + 'px';
      tip.style.left = Math.min(Math.max(4, px - tip.offsetWidth / 2), host.width - tip.offsetWidth - 4) + 'px';
      tip.style.transform = 'translateY(-100%)';
    }
    function hide() { cross.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden'); tip.hidden = true; }
    const nearest = (clientX) => {
      const box = svg.getBoundingClientRect();
      const vx = ((clientX - box.left) / box.width) * W;
      const raw = pts.length === 1 ? 0 : ((vx - L) / (W - L - R)) * span;
      return Math.round(raw);
    };
    svg.addEventListener('pointermove', (e) => show(nearest(e.clientX)));
    svg.addEventListener('pointerleave', hide);
    svg.addEventListener('blur', hide);
    svg.addEventListener('keydown', (e) => {
      const i = pts.findIndex((p) => p[0] === at);
      let next = null;
      if (e.key === 'ArrowRight') next = i < 0 ? pts[pts.length - 1] : pts[Math.min(pts.length - 1, i + 1)];
      else if (e.key === 'ArrowLeft') next = i < 0 ? pts[0] : pts[Math.max(0, i - 1)];
      else if (e.key === 'Home') next = pts[0];
      else if (e.key === 'End') next = pts[pts.length - 1];
      else if (e.key === 'Escape') { hide(); return; }
      if (!next) return;
      e.preventDefault();
      show(next[0]);
    });

    const vals = pts.map((p) => p[1]);
    return h('figure', { class: 'chart', style: 'margin:0' },
      h('p', { class: 'name' }, name),
      h('p', { class: 'latest' }, APRF.fmt(latest), ' ', h('small', {}, 'on ' + when(stream.last))),
      h('p', { class: 'sub' }, pts.length > 1 ? 'Daily range ' + APRF.fmt(Math.min(...vals)) + ' to ' + APRF.fmt(Math.max(...vals)) : 'One day so far'),
      svg, tip);
  }

  // ---- results ----

  function streamCard(st) {
    const cols = st.columns.map(([, k]) => k);
    const meta = [
      h('span', {}, st.source),
      h('span', {}, range(st.first, st.last)),
      h('span', {}, APRF.plural(st.days.length, 'report')),
      st.missing ? h('span', { class: 'gapcount' }, APRF.plural(st.missing, 'day') + ' with no report') : null,
    ];
    const changes = st.changeList.length
      ? h('ul', { class: 'changes' }, st.changeList.map((c) => h('li', {}, h('span', { class: 'chip look' }, icon(ICON.look), 'Look'),
          h('span', {}, h('b', {}, c.name[0].toUpperCase() + c.name.slice(1) + ' ' + c.verb), ' from ' + APRF.fmt(c.from) + ' on ' + when(c.prev) + ' to ' + APRF.fmt(c.to) + ' on ' + when(c.day, true) + '.'))))
      : h('ul', { class: 'changes' }, h('li', {}, h('span', { class: 'chip ok' }, icon(ICON.ok), 'Steady'), h('span', {}, 'No big day-to-day moves in inbox, unwanted, or negative counts.')));
    const table = h('table', {},
      h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, 'Day'), h('th', { scope: 'col' }, 'Segment'), cols.map((c) => h('th', { scope: 'col' }, c)))),
      h('tbody', {}, st.rows.map((row) => row.segment === null
        ? h('tr', { class: 'gaprow' }, h('td', {}, APRF.rowDay(row)), h('td', { colspan: cols.length + 1 }, 'no report'))
        : h('tr', {}, h('td', {}, row.day), h('td', {}, row.segment), row.values.map((v) => h('td', {}, v === null ? '' : APRF.fmt(v)))))));
    return h('article', { class: 'card stream results-enter' },
      h('div', { class: 'stream-head' },
        h('h3', {}, st.domain, ' ', h('span', { class: 'sel' }, 'selector ' + st.selector)),
        h('p', { class: 'meta' }, meta)),
      h('div', { class: 'charts' }, cols.map((_, i) => chart(st, i))),
      h('div', {}, h('p', { class: 'eyebrow', style: 'margin-bottom:8px' }, 'Changes worth a look'), changes),
      st.notes.length ? h('ul', { class: 'notes' }, st.notes.map((t) => h('li', {}, t))) : null,
      h('details', { class: 'table', open: st.rows.length <= 14 }, h('summary', {}, 'Daily table, ' + APRF.plural(st.rows.length, 'row')), h('div', { class: 'tablewrap' }, table)));
  }

  function save(name, text, type) {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const a = h('a', { href: url, download: name });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function copy(text, btn) {
    try { await navigator.clipboard.writeText(text); } catch (e) {
      const ta = h('textarea', { class: 'sr-only' }, text);
      document.body.append(ta); ta.select(); document.execCommand('copy'); ta.remove();
    }
    const label = btn.lastChild.textContent;
    btn.lastChild.textContent = 'Copied';
    setTimeout(() => { btn.lastChild.textContent = label; }, 1600);
  }

  function render() {
    const { loaded, sample } = current;
    const ss = APRF.streams(loaded.reports);
    const first = ss.map((x) => x.first).sort()[0];
    const last = ss.map((x) => x.last).sort().pop();
    const stamp = new Date().toISOString().slice(0, 10);
    const copyBtn = h('button', { class: 'btn', type: 'button', onclick: (e) => copy(APRF.toMarkdown(loaded, VERSION), e.currentTarget) }, icon(ICON.copy), h('span', {}, 'Copy summary'));
    const out = $('results');
    out.replaceChildren(
      h('div', { class: 'toolbar' },
        h('div', {}, h('h2', { id: 'results-title', tabindex: '-1' }, 'Summary'), h('p', {}, APRF.plural(loaded.reports.length, 'report') + ' in ' + APRF.plural(ss.length, 'stream') + ', ' + range(first, last))),
        h('div', { class: 'actions' },
          copyBtn,
          h('button', { class: 'btn', type: 'button', onclick: () => save('aprf-summary-' + stamp + '.html', APRF.toHtml(loaded, VERSION), 'text/html') }, icon(ICON.save), 'Save page'),
          h('button', { class: 'btn', type: 'button', onclick: () => save('aprf-summary-' + stamp + '.json', JSON.stringify(APRF.toDict(loaded), null, 2) + '\n', 'application/json') }, icon(ICON.save), 'Save JSON'),
          h('button', { class: 'link-btn', type: 'button', onclick: clear }, 'Start over'))),
      sample ? h('p', { class: 'sample-note' }, 'This is made-up sample data for example.com: six weeks, two selectors, two days with no report, and a jump in unwanted mail on Sep 25.') : null,
      h('p', { class: 'caveat-note' }, APRF.CAVEAT),
      ...ss.map(streamCard),
      loaded.skipped.length ? h('section', { class: 'card skipped' }, h('p', { class: 'eyebrow' }, 'Skipped, ' + loaded.skipped.length), h('ul', {}, loaded.skipped.map(([w, r]) => h('li', {}, h('span', { class: 'mono' }, w), ': ' + r)))) : null);
    out.hidden = false;
    $('results-title').focus({ preventScroll: true });
    out.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
  }

  function clear() {
    current = null;
    $('results').hidden = true;
    $('results').replaceChildren();
    $('files').value = '';
    $('folder').value = '';
    status('');
    $('pick-files').focus();
  }

  // ---- wiring ----

  $('pick-files').addEventListener('click', () => $('files').click());
  $('pick-folder').addEventListener('click', () => $('folder').click());
  $('files').addEventListener('change', (e) => run([...e.target.files].map((file) => ({ name: file.name, file })), 'files'));
  $('folder').addEventListener('change', (e) => run([...e.target.files].filter((f) => APRF.wanted(f.name)).map((file) => ({ name: file.webkitRelativePath || file.name, file })), 'folder'));
  $('sample').addEventListener('click', () => run([{ name: 'sample.json', file: new Blob([window.APRF_SAMPLE]) }], 'sample'));

  const zone = $('input');
  let depth = 0;
  zone.addEventListener('dragenter', (e) => { e.preventDefault(); depth++; zone.classList.add('over'); });
  zone.addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; zone.classList.remove('over'); } });
  zone.addEventListener('dragover', (e) => e.preventDefault());
  zone.addEventListener('drop', async (e) => {
    e.preventDefault();
    depth = 0;
    zone.classList.remove('over');
    run(await fromDrop(e.dataTransfer), 'drop');
  });
  // A file dropped outside the box should not navigate away from the page
  window.addEventListener('dragover', (e) => e.preventDefault());
  window.addEventListener('drop', (e) => { if (!zone.contains(e.target)) e.preventDefault(); });
})();
