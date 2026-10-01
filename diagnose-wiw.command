#!/bin/zsh
set -eu
cd -- "${0:A:h}"
if [[ -x ../../work/venv/bin/python ]]; then
  portal_python=../../work/venv/bin/python
elif [[ -x .venv/bin/python ]]; then
  portal_python=.venv/bin/python
else
  echo 'Create the Python environment using the README first.'
  exit 1
fi
"$portal_python" scripts/diagnose_wiw.py
