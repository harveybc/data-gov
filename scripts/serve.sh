#!/bin/sh
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
if [ ! -f "$ROOT/var/lake_token" ] || [ ! -f "$ROOT/var/credentials.json" ]; then
  python3 scripts/issue_credentials.py
fi
if [ -f "$ROOT/var/lake_token" ]; then
  DATA_GOV_LAKE_TOKEN="$(tr -d '\n' < "$ROOT/var/lake_token")"
  export DATA_GOV_LAKE_TOKEN
fi
exec python3 -m app.main --load_config examples/config/default.json "$@"
