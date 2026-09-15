#!/bin/bash
# Witness one hard-cast event of the funded world outside the world's own diary.
#
#   witness.sh <launch|dormant|kill|failed_resume> [reason]
#
# Appends one JSON line {world, event, ts, release_digest, ledger_head[, reason]}
# to .witness/<world>.jsonl beside runs/ (append-only, 0600) and, when
# FACTORYLAB_WITNESS_URL is set, POSTs the same line. The append never depends
# on the POST; the POST is best effort and never fails the caller.
#
# The same file is where the runtime itself writes a world's kill line
# (factorylab/runtime/witness.py, with the launch nonce) and where resume looks
# for one. It is deliberately outside runs/: a copy or a restore of the diary
# directory must not carry the witness with it (deploy/README.md, "Witness").
#
# release_digest is the identity of the release on disk now (deploy/README.md,
# "Release identity"); ledger_head is the SHA-256 of the ledger file's bytes at
# this moment, so a witness line commits to a diary prefix without reading it.
# Nothing here opens a key file or prints a summary.
#
# Overridable for rehearsals and tests (never on the funded host):
#   FACTORYLAB_ROOT (/srv/factorylab)  FACTORYLAB_REPO ($root/repo)
#   FACTORYLAB_WORLD (funded)          FACTORYLAB_LEDGER ($root/runs/$world.jsonl)
#   FACTORYLAB_WITNESS_FILE ($root/.witness/$world.jsonl)
#   FACTORYLAB_PYTHON ($repo/.venv/bin/python)
set -eu
umask 077
event=${1:-}
reason=${2:-}
case "$event" in
    launch|dormant|kill|failed_resume) ;;
    *) printf 'witness: unknown event\n' >&2; exit 2 ;;
esac
root=${FACTORYLAB_ROOT:-/srv/factorylab}
repo=${FACTORYLAB_REPO:-$root/repo}
world=${FACTORYLAB_WORLD:-funded}
ledger=${FACTORYLAB_LEDGER:-$root/runs/$world.jsonl}
witness=${FACTORYLAB_WITNESS_FILE:-$root/.witness/$world.jsonl}
python=${FACTORYLAB_PYTHON:-$repo/.venv/bin/python}
# Only closed vocabularies reach the line: a world name and a reason code.
[[ $world =~ ^[a-z0-9][a-z0-9_-]{0,63}$ ]] || { printf 'witness: bad world\n' >&2; exit 2; }
if [[ -n $reason && ! $reason =~ ^[a-z_]{1,40}$ ]]; then
    reason=none
fi
digest=$(cd "$repo" && "$python" -m factorylab.runtime.release --digest 2>/dev/null) || digest=""
[[ $digest =~ ^[0-9a-f]{64}$ ]] || digest=unavailable
if [[ -f $ledger ]]; then
    if command -v sha256sum > /dev/null 2>&1; then
        head=$(sha256sum "$ledger" | cut -d' ' -f1)
    else
        head=$(shasum -a 256 "$ledger" | cut -d' ' -f1)  # a rehearsal on macOS
    fi
else
    head=absent
fi
ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
line=$(printf '{"world":"%s","event":"%s","ts":"%s","release_digest":"%s","ledger_head":"%s"' \
    "$world" "$event" "$ts" "$digest" "$head")
if [[ -n $reason ]]; then
    line="$line$(printf ',"reason":"%s"' "$reason")"
fi
line="$line}"
# Append first: the local file is the record; the receiver is a copy.
mkdir -p -m 700 "$(dirname "$witness")"
printf '%s\n' "$line" >> "$witness"
[[ -n ${FACTORYLAB_WITNESS_URL:-} ]] || exit 0
url=$FACTORYLAB_WITNESS_URL
# HTTPS anywhere, plain HTTP only to the loopback rehearsal receiver; no
# characters with curl-config syntax significance, and the URL never appears in
# the argument list or the logs.
case "$url" in
    https://*|http://127.0.0.1:*|http://127.0.0.1/*|http://localhost:*|http://localhost/*) ;;
    *) exit 0 ;;
esac
if [[ $url == *'"'* || $url == *'\'* || $url == *$'\n'* || $url == *$'\r'* ]]; then
    exit 0
fi
printf 'url = "%s"\n' "$url" |
    curl --config - --silent --fail --max-time 20 --proto '=https,http' \
        --header 'Content-Type: application/json' \
        --data-binary "$line"$'\n' \
        --output /dev/null 2>/dev/null || true
exit 0
