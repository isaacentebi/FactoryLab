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
# Fix the byte limit before copying. Discard only an unfinished final record.
# Keys are copied by the unattended process, never displayed or embedded in the image.
/usr/bin/python3 - "$root" "$stage" <<'PY'
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
PY
# No unencrypted tar is ever created. The stage is root-only in systemd's PrivateTmp.
tar -C "$stage" -cf - runs openrouter.key hyperliquid.key reserve.key |
    age --encrypt --recipient "$AGE_RECIPIENT" --output "$stage/backup.tar.age"
name="factorylab-$(date -u +%Y%m%dT%H%M%SZ).tar.age"
rclone copyto "$stage/backup.tar.age" "${BACKUP_REMOTE%/}/$name" \
    --config "$RCLONE_CONFIG" --retries 5 --low-level-retries 10 --quiet
