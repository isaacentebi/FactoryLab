# Hosting the funded world and its wake

This directory prepares a single Ubuntu 24.04 LTS droplet. It does **not** authorize
launch or supply a funded manifest. Finish the handoff's launch gates before the
one funded launch. Do not modify a running world, refill its seed credits, update
its checkout, or restore an older ledger over it.

## Before creating the droplet

1. Select a reviewed **40-character commit SHA** containing this deployment,
   resume, and the approved `worlds/funded.toml`. Record the SHA, manifest hash,
   launch balances, declared drip and covenant outside the machine. The current
   workstream cannot pin its own future merge commit; `PINNED_COMMIT` in
   `cloud-init.yaml` is intentionally a required substitution. Never use `main`,
   a moving tag, or the pre-deployment parent commit.
2. Start with a Basic droplet, 2 vCPU / 4 GiB RAM, Ubuntu 24.04 LTS, in a region
   with reliable venue access. Select SSH-key authentication, no password login.
   Measure a representative rehearsal's ledger growth and peak memory; choose
   disk capacity for a year **before** launch. Snapshots and the five aggregate
   scans grow with the ledger. This implementation neither rotates evidence nor
   promises that a small fixed disk holds an arbitrarily long experiment.

   Memory is also unbounded with diary length. `Ledger.reopen` retains encrypted
   tokens and temporarily holds the file bytes plus all decrypted item objects;
   `resume` then materializes the diary again to select the last snapshot and
   replay tail. Each wake aggregate similarly materializes every item. The
   working-space bound is O(L + D + S + T + A), where L is encrypted diary size,
   D the Python objects for its decrypted contents (including every historical
   snapshot), S restored runtime state, T replay-tail objects, and A aggregate
   output. Streaming only the loop in `resume` or `wake` cannot remove the
   kernel reader's full-file allocation. A bounded reader/aggregate API requires
   a separate kernel-ledger change; this FC pass uses the documentation fallback.

   The FC synthetic reader probe (1,000-character nested payloads) measured
   approximately linear growth when doubling item count; this is an allocation
   test, not a production sizing ratio. The cold audit measured 56 MB RSS for a
   6.2 MB diary on its fixture. Neither coefficient is a guaranteed upper bound:
   Python object overhead, nested snapshots, pending decisions and output series
   vary by world. Budget RAM for the maximum intended diary at simultaneous
   runtime + wake + backup load, and measure peak RSS in the pinned rehearsal.
   A 4 GiB host does not support an indefinitely growing diary; disk capacity
   alone cannot establish that resume and wake will remain available.
3. Prepay: in DigitalOcean's team **Billing**, choose **Add funds**, select a
   supported payment method and deposit at least 12 times the droplet's displayed
   monthly price, plus tax, backup storage, expected transfer and a margin. Verify
   the payment has settled and the available prepaid balance covers the year.
   DigitalOcean bills monthly and applies prepaid funds first; this is account
   credit, not a one-year reserved instance or a guarantee against price changes.
   Use a dedicated billing team so other resources cannot consume this credit.
   Prepay the backup storage and webhook hosting separately if applicable.
   See [DigitalOcean's payment instructions](https://docs.digitalocean.com/platform/billing/pay-bills/).
4. On an offline operator machine, run `age-keygen -o factory-backup.identity`.
   Save the identity in two offline locations. Put only the printed `age1...`
   **public recipient** in `ops.env` below. The private identity never goes on
   the droplet. See [age](https://github.com/FiloSottile/age).
5. Configure an `rclone` remote in a local temporary config using `rclone config
   --config ./factory-rclone.conf`. Give it a dedicated bucket/prefix and an
   unattended credential whose lifetime covers the run. Verify upload and restore
   permissions before launch. Enable storage-side versioning and a predeclared
   retention policy; the script never deletes remote backups. Budget storage for
   nightly full archives, not incremental backups.

## Provision (no world starts yet)

Copy `cloud-init.yaml` and replace `PINNED_COMMIT` with the selected SHA and
`REPO_URL` with a cloneable release source. Upload that file as droplet user-data.
No wallet key, repository token, webhook, backup credential or private age
identity belongs in user-data, an image, Git or shell tracing.

For this private repository, use `/srv/factorylab/source.bundle` as `REPO_URL`.
Prepare a bundle locally from the reviewed checkout:

```sh
git bundle create /tmp/factorylab-release.bundle --all
```

Cloud-init's first clone will fail until the bundle is present; this leaves the
machine unstarted. After cloud-init finishes, copy the bundle and rerun the
provisioner once, before any world exists:

```sh
scp /tmp/factorylab-release.bundle root@DROPLET:/srv/factorylab/source.bundle
ssh root@DROPLET /usr/local/sbin/factorylab-provision
```

Alternatively a public, credential-free release clone URL provisions directly.
The provisioner checks out the exact SHA detached, installs pinned uv 0.12.13,
installs Python 3.13 through uv in `/opt/factorylab-python`, synchronizes the lock
with `uv sync --frozen`, runs the gate, and then runs `deploy/jail-check.sh`: the
population jail (bubblewrap with the unit's `NoNewPrivileges`, `ProtectSystem=strict`,
`ProtectHome`, `PrivateTmp`, and Ubuntu 24.04's AppArmor policy on unprivileged user
namespaces) must start a confined interpreter **as the `factory` user**, or the
provisioner exits non-zero with the reason. The gate's own jail tests run as root and
prove nothing about that user; a world offering population tools refuses to launch
without a working jail. Python and package patch versions are
selected once during provisioning; record them with the launch record. No sync,
Git fetch or dependency upgrade occurs on service restarts. The standard-library
static server and backup helper use Ubuntu's system Python; the factory uses the
uv-managed 3.13 environment.

Verify on the host:

```sh
cloud-init status --long
git -C /srv/factorylab/repo rev-parse HEAD
/srv/factorylab/repo/.venv/bin/python --version
systemd-analyze verify /etc/systemd/system/factorylab*.service /etc/systemd/system/factorylab*.timer
bash /srv/factorylab/repo/deploy/jail-check.sh
```

The last line must print `factorylab jail-check: ok (...)`; any other output names
the stage at which the jail failed (executable, interpreter prefix, launch, or the
confined interpreter's own exit and streams). `start.sh` repeats the probe before
the first `run` and exits 3 (final, alerted) rather than looping on a refusal.

For the bundle flow, the cloud-init error is expected; require the manual
provisioner to exit 0. The service and timers remain disabled until launch.
Automatic apt upgrade timers are disabled to preserve the precommitted machine;
perform all desired OS maintenance and reboot **before** launch. OS security
updates after launch require a new-world decision under the covenant.

## Place keys and operating configuration

The operator copies the existing keys over authenticated SSH, without printing
values or placing them in command arguments. For example, run locally:

```sh
scp /secure/location/openrouter.key /secure/location/hyperliquid.key /secure/location/reserve.key root@DROPLET:/srv/factorylab/
scp ./factory-rclone.conf root@DROPLET:/srv/factorylab/rclone.conf
```

Then on the droplet:

```sh
chown factory:factory /srv/factorylab/openrouter.key /srv/factorylab/hyperliquid.key /srv/factorylab/reserve.key
chmod 0600 /srv/factorylab/openrouter.key /srv/factorylab/hyperliquid.key /srv/factorylab/reserve.key
chown root:root /srv/factorylab/rclone.conf
chmod 0600 /srv/factorylab/rclone.conf
install -o root -g root -m 0600 /dev/null /srv/factorylab/ops.env
```

Use a secure editor to populate `/srv/factorylab/ops.env` (systemd env syntax, not
shell code). Replace these sample values; do not put real values into this README:

```ini
AGE_RECIPIENT=age1_REPLACE_WITH_PUBLIC_RECIPIENT
BACKUP_REMOTE=factory-backups:bucket/factorylab
RCLONE_CONFIG=/srv/factorylab/rclone.conf
FACTORY_WEBHOOK_URL=https://REPLACE_WITH_RECEIVER
```

The receiver accepts precisely one JSON line such as
`{"world":"funded","event":"terminated"}` or
`{"world":"funded","event":"failed_resume"}`. It gets no reason, exception,
summary, ledger path, balances, key or model content. HTTPS is required; redirects
are not followed. Alert delivery is best effort (20-second timeout); failure
cannot revive a terminated world. systemd loads the env file; scripts never
source arbitrary shell from it. Test the receiver before launch using a synthetic
payload, and configure its own retention and uptime before the covenant begins.

The CLI loads the usual root key files; the ledger's existing
`runs/funded.jsonl.key` is created by `run`, mode 0600, and reused privately by
resume and wake. No new viewing key is generated. Wake never releases the public
seal. Do not use `postmortem`, `report`, summaries or key inspection as a live view.
The web server has a separate dynamic user and no access to the key files or runs.
Keep `www` exclusively for `wake.html` and `wake.json`; never symlink private
files into it.

## Rehearse, start once, verify, leave it alone

Run a separate disposable scripted/testnet rehearsal before launching funded,
including process death, restart, final exit, backup restore and webhook receipt.
Do not test kills, edits or restore procedures on the funded world. On the final
host verify configured modes with `stat`, without reading key values. Validate
the approved manifest as the factory user:

```sh
cd /srv/factorylab/repo
sudo -u factory .venv/bin/factorylab manifest --world funded
```

Compare the hash with the precommitted launch record. Confirm one year of prepaid
hosting, remote storage and credential lifetimes, then start once:

```sh
systemctl enable --now factorylab.service
systemctl enable --now factorylab-static.service factorylab-wake.timer factorylab-backup.timer
systemctl start factorylab-wake.service
systemctl start factorylab-backup.service
systemctl is-active factorylab.service factorylab-static.service
systemctl list-timers 'factorylab-*'
ss -ltn 'sport = :8080'
```

The socket must be `127.0.0.1:8080`, never `0.0.0.0` or a public IPv6 address.
A firewall should allow SSH only from the operator's address; do not open 8080.
Check the wake through a local SSH tunnel:

```sh
ssh -N -L 8080:127.0.0.1:8080 root@DROPLET
```

Open `http://127.0.0.1:8080/wake.html` locally. The page makes no external requests.
Check world/hash, balances and event time against the launch expectations. This
is the only live experiment view. Thereafter the operator may read this wake;
do not inspect the interior or change anything. systemd resumes the same ledger
after process failure/reboot. There is no polling agent making intervention
choices, no live service upgrade, and no automatic replacement world.

### Exit-code contract

| Path | Exit | Supervisor behavior |
| --- | --- | --- |
| Resume returns a live summary | 0 | Restart with the same saved budget |
| Resume finds an authenticated Terminated event, or terminates while running | 3 | Final success; no restart; termination webhook |
| Resume fails authentication, replay, credentials or provider setup | 1 | Failed-resume webhook; restart with backoff |

`run` retains its existing code 0; the wrapper immediately enters `resume` after
it returns, which recognizes termination without another world event. Every
restart with an existing ledger enters resume directly. A crash requires one
recovery attempt to learn finality. An invalid chain is never treated as proof of
termination. A missing ledger with an existing ledger key fails safely; neither
script deletes evidence to make launch work.

Ubuntu 24.04's systemd supports `RestartSteps`: delay begins at 30 seconds and
increases across six steps to 15 minutes. Start-rate limiting is disabled so a
long provider outage does not permanently strand the world. The funded run uses
`2**63 - 1` events as a practical unbounded launch budget, preserves the manifest's
clock, seed and drip, and does not set `--kill-at-end`. It does not override funds.
The finite-budget resume behavior is unchanged. Failed run output and all runtime
summaries are discarded, and core dumps are disabled. Do not enable verbose
service logging after launch.

### Wake schema and decisions

Exactly these fields are published: `wallet_series`, `spend_by_capability`,
`invocations_by_assembly`, `action_frequencies`, `settlement_latency`, `world`,
`manifest_hash`, `uptime_ns`, `last_event_time_ns`, plus `venue` when a venue key is
present and `reserve` when a reserve key is present. The five views are returned
by `Ledger.aggregate`, each verifying the same frozen chain. Incomplete input or
verification failure retries once after 100 ms; a second failure replaces all
ledger-derived fields with `"unavailable"` and exits 1. Optional account failures
mark only their unavailable fields. There is no exception text in the artifacts.
Each output file is atomically replaced; the pair is not a transactional bundle.

The snapshot adapter is isolated in `runtime/wake.py` and couples to Ledger's
private read state because its public reopen API is a writer-only recovery API.
It reads the existing adjacent key as the unattended process, never exposes it,
and only inspects event timestamps for timing. Aggregates are not reimplemented
outside the kernel. The genesis must match a manifest in the pinned checkout's
`worlds/`; an unknown or command-line-modified manifest reports unavailable.

Live uptime means elapsed time since the first recorded Tick, includes downtime,
and stops at the terminal event. Before the first tick it is zero. Scripted
uptime and event time are simulated nanoseconds from launch. Event time is the
last recorded event's timestamp, not file mtime or last wallet movement.

Venue equity and realized amounts are integer micro-USD; position sizes and entry
prices are decimal strings. `realized_to_date_micro` is gross closed trading P&L
from venue fills over the account's retained history (fees/funding excluded), not
wallet growth. The query paginates with an overlapping timestamp boundary; if
history reaches the venue's 10,000-fill retention cap, or a saturated timestamp
cannot be paginated safely, it reports unavailable. It never presents the latest
page as an all-time sum. See [Hyperliquid's history limits](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint).
Reserve fields are `usdc_micro` and `venice_micro`; no address, ETH balance or
payment authorization is published. The inline SVG uses integer geometry; all
ledger series values remain in JSON and the page. Strings are HTML-escaped and
CSP prohibits scripts, embedding and network resources.

### Backup and restore rehearsal

At 03:00 UTC nightly (up to ten minutes jitter; missed runs catch up), `backup.sh`
captures the ledger's byte length and copies only complete newline-terminated
records from that prefix. The factory continues appending. Keys are immutable;
the process copies the three root keys plus `runs/funded.jsonl.key` into a private
root-only temporary directory. It pipes tar directly into age; no plaintext tar
is written. It uploads a dated `.tar.age` through
[rclone copyto](https://rclone.org/commands/rclone_copyto/) and removes local staging
on exit. A failed upload fails the backup unit; the next nightly timer runs again.
Only ciphertext goes to the remote. Keep the SHA/manifest and operations config
in the operator's prelaunch recovery record; provider keys and ledger are in the
archive, operations credentials are not.

Before funded launch, download and decrypt a **rehearsal** backup on an isolated
machine with the offline age identity. Extract privately, verify the ledger via
wake and exercise scripted resume. Never run a restored funded copy alongside
the original; that would create two writers for one wallet. Remote restore or
machine replacement is not automated here, and human disaster recovery after
launch remains outside the covenant. Prepayment and restart/backup units cannot
guarantee survival of permanent host loss, exhausted disk, revoked credentials,
a year beyond prepaid capacity or a hung process that never exits. These are
explicit limits of this hosting chunk, not claims of durable live acceptance.
