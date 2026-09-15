#!/usr/bin/env bash
# Package the repository at HEAD for an outside reviewer: tracked files only, no keys, no
# runs, no venv. Usage: scripts/package_review.sh <out.zip>
set -euo pipefail
out="${1:?usage: package_review.sh <out.zip>}"
head="$(git rev-parse HEAD)"
tmp="$(mktemp -d)"
git archive --format=tar --prefix="FactoryLab-${head:0:7}/" HEAD | tar -x -C "$tmp"
# Belt and braces: no key file may ride along even if one were ever tracked by mistake.
find "$tmp" -name '*.key' -delete
( cd "$tmp" && zip -qr "$OLDPWD/$out" . )
rm -rf "$tmp"
echo "packaged HEAD ${head} -> ${out}"
