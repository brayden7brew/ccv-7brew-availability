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
"$portal_python" -c 'from app.config import settings; c=settings(); assert c.wiw_account_id == 4319477 and c.wiw_context_user_id == 53517822, "Test workplace required"'
"$portal_python" -m app.cli reconcile --id 3 --manager-email brayden@portal.test --outcome not-applied --note 'Read-only WIW verification matches the saved pre-write state; no changes applied.'
echo 'Request #3 reconciled. No WIW writes were performed.' 
