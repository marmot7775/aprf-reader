#!/usr/bin/env node
// node web/test/run.js --json|--md|--html <files...> [-]: the web page's reading of
// files, printed like the command line prints them. tests/test_web_parity.py
// compares the two.
'use strict';
const fs = require('fs');
const APRF = require('../src/aprf.js');

(async () => {
  const [mode, ...paths] = process.argv.slice(2);
  // "-" is a paste, read from stdin and read last, as the command line does
  const files = paths.filter((p) => p !== '-').map((p) => ({ name: p, bytes: new Uint8Array(fs.readFileSync(p)) }));
  if (paths.includes('-')) {
    const bytes = new Uint8Array(fs.readFileSync(0));
    files.push({ name: APRF.pastedName(bytes), bytes });
  }
  const loaded = await APRF.load(files);
  const version = process.env.APRF_VERSION || '';
  process.stdout.write(mode === '--json' ? JSON.stringify(APRF.toDict(loaded)) : mode === '--html' ? APRF.toHtml(loaded, version) : APRF.toMarkdown(loaded, version));
})();
