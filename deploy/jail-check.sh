#!/bin/bash
# Prove the population jail starts as the factory user under the world unit's own
# restrictions (NoNewPrivileges, ProtectSystem=strict, ProtectHome, PrivateTmp, AppArmor's
# unprivileged user-namespace policy on Ubuntu 24.04). Exit status is the probe's: 0 only
# when a confined interpreter answered; otherwise the reason is printed and provisioning
# (or the first launch) must stop rather than promise tools the world cannot run.
set -eu
python=/srv/factorylab/repo/.venv/bin/python
exec systemd-run --wait --pipe --collect --quiet \
    --unit "factorylab-jail-check-$$" \
    -p User=factory -p Group=factory -p WorkingDirectory=/srv/factorylab \
    -p UMask=0077 -p LimitCORE=0 \
    -p NoNewPrivileges=true -p PrivateTmp=true -p ProtectSystem=strict -p ProtectHome=true \
    -p ReadWritePaths=/srv/factorylab/runs \
    "$python" -m factorylab.cortex.sandbox
