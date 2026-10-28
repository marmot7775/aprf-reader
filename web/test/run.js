#!/usr/bin/env node
// node web/test/run.js --json|--md|--html <files...>: the web page's reading of
// files, printed like the command line prints them. tests/test_web_parity.py
// compares the two.
'use strict';
const fs = require('fs');
const APRF = require('../src/aprf.js');

(async () => {
  const [mode, ...paths] = process.argv.slice(2);
  const loaded = await APRF.load(paths.map((p) => ({ name: p, bytes: new Uint8Array(fs.readFileSync(p)) })));
  const version = process.env.APRF_VERSION || '';
  process.stdout.write(mode === '--json' ? JSON.stringify(APRF.toDict(loaded)) : mode === '--html' ? APRF.toHtml(loaded, version) : APRF.toMarkdown(loaded, version));
})();
