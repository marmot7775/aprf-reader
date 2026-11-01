#!/bin/bash
# Installs what the nightly fix run needs to run this repository's tests.
set -euo pipefail
python -m pip install --upgrade pip
pip install -e '.[dev]'
