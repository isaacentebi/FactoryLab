#!/bin/bash
set -eu
cd /srv/factorylab
ledger=/srv/factorylab/runs/funded.jsonl
cli=/srv/factorylab/repo/.venv/bin/factorylab
witness=/srv/factorylab/repo/deploy/witness.sh
# systemd owns the directory. Only the mode and one reason code are recorded,
# never a summary. A stale code must not be reported for a new failure.
: > /run/factorylab/reason
# The witness file is the world's hard casts seen from outside its diary:
# launch, final termination (kill) and every refused resume, each with the
# release digest on disk and the ledger's byte hash. Best effort, never fatal.
witness() {
    bash "$witness" "$@" || true
}
reason_code() {
    local line=""
    [[ -f /run/factorylab/reason ]] && read -r line < /run/factorylab/reason || true
    printf '%s' "$line"
}
# Launch once, then witness it; a refused launch leaves nothing to witness.
launch() {
    printf 'run\n' > /run/factorylab/mode
    if "$cli" run --world funded --events 9223372036854775807 --ledger "$ledger"; then
        witness launch
    fi
}
# Resume, and witness what resume learned: 3 is finality (the operator's kill or
# the world's own death), 1 is a refused resume with its one reason code.
resume() {
    printf 'resume\n' > /run/factorylab/mode
    code=0
    "$cli" resume --world funded --ledger "$ledger" || code=$?
    case "$code" in
        3) witness kill ;;
        1) witness failed_resume "$(reason_code)" ;;
    esac
    return "$code"
}
if [[ ! -e "$ledger" ]]; then
    # A first launch on a host whose jail cannot start is final (exit 3), not a
    # restart loop: run would refuse the manifest on every attempt.
    /srv/factorylab/repo/.venv/bin/python -m factorylab.cortex.sandbox || exit 3
    launch || true
fi
# Also checks finality immediately when run returns; run always exits 0.
code=0
resume || code=$?
if [[ "$code" -eq 5 ]]; then
    # Authenticated startup evidence contains no Launch: no world exists yet.
    launch || true
    code=0
    resume || code=$?
fi
exit "$code"
