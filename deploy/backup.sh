#!/bin/bash
# ops.env is loaded by systemd, not executed as shell code.
set -euo pipefail
umask 077
: "${AGE_RECIPIENT:?Set AGE_RECIPIENT in ops.env}"
: "${BACKUP_REMOTE:?Set BACKUP_REMOTE in ops.env}"
: "${RCLONE_CONFIG:?Set RCLONE_CONFIG in ops.env}"
root=/srv/factorylab
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
mkdir "$stage/runs"
# The release that is running beside this ledger: its digest and the three
# inputs that make it. A restored ledger resumes only under this digest, so
# the backup names the release it needs (deploy/README.md, "Release identity").
python="$root/repo/.venv/bin/python"
[[ -x "$python" ]] || python=/usr/bin/python3  # the module needs only the standard library
if ! (cd "$root/repo" && PYTHONPATH="$root/repo" "$python" -m factorylab.runtime.release) \
        > "$stage/runs/funded.release.json" 2> /dev/null; then
    # A rehearsal root without the package still archives, and says the digest is missing.
    printf '{"release_digest": "unavailable"}\n' > "$stage/runs/funded.release.json"
fi
# The witness file (launch, kill, failed_resume lines) travels with the ledger.
if [[ -f "$root/runs/funded.witness.jsonl" ]]; then
    cp "$root/runs/funded.witness.jsonl" "$stage/runs/funded.witness.jsonl"
fi
# Fix the byte limit before copying. Discard only an unfinished final record.
# Keys are copied by the unattended process, never displayed or embedded in the image.
/usr/bin/python3 - "$root" "$stage" <<'PY'
import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path

root, stage = map(Path, sys.argv[1:])
source = root / 'runs/funded.jsonl'
with source.open('rb') as stream, (stage / 'runs/funded.jsonl').open('wb') as out:
    remaining = os.fstat(stream.fileno()).st_size
    while remaining:
        line = stream.readline(remaining)
        if not line:
            raise RuntimeError('ledger truncated during backup')
        remaining -= len(line)
        if line.endswith(b'\n'):
            out.write(line)
for relative in ('runs/funded.jsonl.key', 'openrouter.key', 'hyperliquid.key', 'reserve.key'):
    source = root / relative
    mode = source.lstat().st_mode
    if not stat.S_ISREG(mode) or stat.S_IMODE(mode) != 0o600:
        raise RuntimeError('key must be a regular 0600 file')
    shutil.copyfile(source, stage / relative)
    (stage / relative).chmod(0o600)
# The same ledger can only resume under its exact launch manifest. It may be an
# uncommitted, content-hashed release, so a Git checkout alone cannot recover it.
relative = Path('repo/worlds/funded.toml')
source = root / relative
if not stat.S_ISREG(source.lstat().st_mode):
    raise RuntimeError('funded manifest must be a regular file')
(stage / relative.parent).mkdir(parents=True)
(stage / 'repo').chmod(0o755)
(stage / 'repo/worlds').chmod(0o755)
shutil.copyfile(source, stage / relative)
(stage / relative).chmod(0o644)
# The ledger bytes the archive holds, so the release record can be checked
# against a restore without decrypting anything.
copied = stage / 'runs/funded.jsonl'
record = json.loads((stage / 'runs/funded.release.json').read_text())
record['ledger'] = {'bytes': copied.stat().st_size,
                    'sha256': hashlib.sha256(copied.read_bytes()).hexdigest()}
(stage / 'runs/funded.release.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
PY
# No unencrypted tar is ever created. The stage is root-only in systemd's PrivateTmp.
tar -C "$stage" -cf - runs repo openrouter.key hyperliquid.key reserve.key |
    age --encrypt --recipient "$AGE_RECIPIENT" --output "$stage/backup.tar.age"
name="factorylab-$(date -u +%Y%m%dT%H%M%SZ).tar.age"
rclone copyto "$stage/backup.tar.age" "${BACKUP_REMOTE%/}/$name" \
    --config "$RCLONE_CONFIG" --retries 5 --low-level-retries 10 --quiet
