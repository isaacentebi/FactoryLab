"""The event loop that puts the other packages together, and the operator's CLI.

Owns the manifest reader, bootstrap, the loop and its step methods, routing,
governance, the pricing windows, feedback, venue writes, the immune organ, live
pacing, recovery from an interrupted process, the public wake, and the command
line. It owns no money, no scores and no rules of its own: it is glue over
kernel physics.

Imports every other package, and is imported by ``charter`` and ``cortex`` only
for vocabularies it owns. Nothing outside this package starts a world.
"""

# Loading the runtime installs the kill witness into the kernel's one hook, so a
# kill reached through any runtime entry point (the loop, the CLI's ``kill``, a
# reopened ledger under ``Termination``) is recorded outside the diary.
from factorylab.runtime import witness as _witness  # noqa: E402, F401
