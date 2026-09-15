#!/bin/bash
# Record the release identity of this checkout beside it.
#
#   install.sh [checkout-root]      (default: the repository this script is in)
#
# Run once by the provisioner after `uv sync --frozen`. It writes RELEASE, a JSON
# record of the git head, the uv.lock hash, the tree hash of factorylab/ and the
# digest they make. The running factory recomputes the lock and tree hashes every
# start and reads the head from git; RELEASE is the head's fallback on a host
# without git, and the launch record the operator keeps off the machine. Nothing
# here touches runs/, keys or the manifest.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=${1:-$(cd "$here/.." && pwd)}
python=${FACTORYLAB_PYTHON:-$repo/.venv/bin/python}
# The module comes from the installed environment, never from the target's tree.
cd "$here"
"$python" -m factorylab.runtime.release --root "$repo" --write "$repo/RELEASE" > /dev/null
chmod 0644 "$repo/RELEASE"
"$python" -m factorylab.runtime.release --root "$repo" --digest
