#!/bin/bash
# The full test suite plus the web build, as the nightly fix run calls it.
set -euo pipefail
python -m pytest -q
node web/build.js
