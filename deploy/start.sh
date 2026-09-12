#!/bin/bash
set -eu
cd /srv/factorylab
ledger=/srv/factorylab/runs/funded.jsonl
cli=/srv/factorylab/repo/.venv/bin/factorylab
# systemd owns the directory. Only the mode is recorded, never a summary.
if [[ ! -e "$ledger" ]]; then
    printf 'run\n' > /run/factorylab/mode
    "$cli" run --world funded --events 9223372036854775807 --ledger "$ledger" || true
fi
printf 'resume\n' > /run/factorylab/mode
# Also checks finality immediately when run returns; run's legacy exit code is 0.
code=0
"$cli" resume --world funded --ledger "$ledger" || code=$?
if [[ "$code" -eq 5 ]]; then
    # Authenticated startup evidence contains no Launch: no world exists yet.
    printf 'run\n' > /run/factorylab/mode
    "$cli" run --world funded --events 9223372036854775807 --ledger "$ledger" || true
    printf 'resume\n' > /run/factorylab/mode
    exec "$cli" resume --world funded --ledger "$ledger"
fi
exit "$code"
