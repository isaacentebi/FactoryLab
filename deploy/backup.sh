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
# The witness file (launch, dormant, kill, failed_resume lines) is archived for
# the post-mortem under witness/, never under runs/: it is the record a restored
# diary must not carry, and the host reads it only from .witness/ beside runs/.
# Restoring an archive never puts an older witness where resume would read it
# (deploy/README.md, "Witness").
if [[ -f "$root/.witness/funded.jsonl" ]]; then
    mkdir "$stage/witness"
    cp "$root/.witness/funded.jsonl" "$stage/witness/funded.jsonl"
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
runs = root / 'runs'


def hash_named(name):
    return len(name) == 64 and all(c in '0123456789abcdef' for c in name)


# The diary names bytes it keeps beside itself (wave 17): the one rolling checkpoint
# (runs/funded.checkpoint/), the artifact archive (runs/funded.artifacts/, edition 2,
# C9) and the recorded answers too large to ride inline (runs/funded.io/). A resume
# refuses a diary whose checkpoint or artifacts are missing (checkpoint_missing,
# artifact_missing), so the archive must hold the ones the *copied* diary names.
# The world keeps writing while this runs: it replaces its checkpoint and collects
# released artifacts at every window boundary. So every hash-named file of the two
# sidecars that remove files is pinned with a hard link, once just before the
# diary's length is fixed and once after the diary is copied, and the archive is
# taken from the pin. Bytes precede the item naming them, and nothing the copied
# diary's latest checkpoint names is removed until a later checkpoint is in the
# diary, so each such file exists at one of the two instants; a hard link keeps its
# bytes whatever the world unlinks afterwards. The pin is on the same filesystem as
# the files, beside them, and is removed on exit.
PINNED = ('funded.artifacts', 'funded.checkpoint')
pin = runs / '.backup-pin'
if pin.exists():
    shutil.rmtree(pin)  # a pin an interrupted backup left: never a source of truth


def pin_sidecars():
    for name in PINNED:
        source = runs / name
        if not source.is_dir():
            continue
        target = pin / name
        target.mkdir(parents=True, exist_ok=True)
        for entry in source.iterdir():
            # An in-progress temporary (.<prefix>-*) is never a name the diary uses.
            if not hash_named(entry.name) or (target / entry.name).exists():
                continue
            try:
                if stat.S_ISREG(entry.lstat().st_mode):
                    os.link(entry, target / entry.name)
            except FileNotFoundError:
                continue  # removed between the listing and the link: named by no copy


try:
    pin_sidecars()
    # The ledger is hashed as it is copied, one record at a time: never read whole.
    source = runs / 'funded.jsonl'
    digest, copied = hashlib.sha256(), 0
    with source.open('rb') as stream, (stage / 'runs/funded.jsonl').open('wb') as out:
        remaining = os.fstat(stream.fileno()).st_size
        while remaining:
            line = stream.readline(remaining)
            if not line:
                raise RuntimeError('ledger truncated during backup')
            remaining -= len(line)
            if line.endswith(b'\n'):
                out.write(line)
                digest.update(line)
                copied += len(line)
    pin_sidecars()
    totals = {}
    for name in PINNED:
        count = size = 0
        pinned = pin / name
        if pinned.is_dir():
            (stage / 'runs' / name).mkdir()
            for entry in sorted(pinned.iterdir()):
                data = entry.read_bytes()
                # An artifact is named by the SHA-256 of its bytes; a checkpoint is sealed
                # under the diary key, and a restore checks it against the diary's hash.
                if name == 'funded.artifacts' and hashlib.sha256(data).hexdigest() != entry.name:
                    raise RuntimeError('artifact bytes do not match their hash')
                (stage / 'runs' / name / entry.name).write_bytes(data)
                (stage / 'runs' / name / entry.name).chmod(0o600)
                count += 1
                size += len(data)
        totals[name] = {'count': count, 'bytes': size}
    # Recorded answers are content-addressed and never removed, so every one the
    # copied diary names is already on disk: copied after the diary, not pinned.
    count = size = 0
    answers = runs / 'funded.io'
    if answers.is_dir():
        (stage / 'runs/funded.io').mkdir()
        for entry in sorted(answers.iterdir()):
            if not hash_named(entry.name) or not stat.S_ISREG(entry.lstat().st_mode):
                continue
            shutil.copyfile(entry, stage / 'runs/funded.io' / entry.name)
            (stage / 'runs/funded.io' / entry.name).chmod(0o600)
            count += 1
            size += entry.stat().st_size
    totals['funded.io'] = {'count': count, 'bytes': size}
finally:
    if pin.exists():
        shutil.rmtree(pin)
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
record = json.loads((stage / 'runs/funded.release.json').read_text())
record['ledger'] = {'bytes': copied, 'sha256': digest.hexdigest()}
record['artifacts'] = totals['funded.artifacts']
record['checkpoint'] = totals['funded.checkpoint']
record['io'] = totals['funded.io']
(stage / 'runs/funded.release.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
PY
# The staged copy is proven restorable before it leaves the host, by the release's
# own interpreter: the copied diary's latest checkpoint is beside it and hash-true,
# every artifact that checkpoint names is there, and every recorded answer its
# replay tail names is there (runtime/sidecar.py, ``verify_restorable``). A copy that
# cannot be proven is never uploaded: a backup nobody proved restorable must not
# look like, or rotate out, one that was. With no release interpreter there is no
# proof, so the backup fails loudly rather than uploading an unverified copy.
verifier="$root/repo/.venv/bin/python"
if [[ ! -x "$verifier" ]]; then
    echo "backup not uploaded: no release interpreter at $verifier to prove the copy restorable" >&2
    exit 1
fi
if ! (cd "$root/repo" && PYTHONPATH="$root/repo" "$verifier" -m factorylab.runtime.sidecar \
        "$stage/runs/funded.jsonl" "$stage/repo/worlds/funded.toml") > /dev/null 2>&1; then
    echo "backup not uploaded: the staged copy is not restorable" >&2
    exit 1
fi
# No unencrypted tar is ever created. The stage is root-only in systemd's PrivateTmp.
members=(runs repo openrouter.key hyperliquid.key reserve.key)
[[ -d "$stage/witness" ]] && members+=(witness)
tar -C "$stage" -cf - "${members[@]}" |
    age --encrypt --recipient "$AGE_RECIPIENT" --output "$stage/backup.tar.age"
name="factorylab-$(date -u +%Y%m%dT%H%M%SZ).tar.age"
rclone copyto "$stage/backup.tar.age" "${BACKUP_REMOTE%/}/$name" \
    --config "$RCLONE_CONFIG" --retries 5 --low-level-retries 10 --quiet
