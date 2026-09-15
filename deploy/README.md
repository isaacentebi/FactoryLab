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

   Disk ledgers already stream. `Ledger.reopen` verifies the persisted bytes in
   chunks, uses an authenticated head when available, and decrypts records one at
   a time. Recovery finds the latest snapshot backwards and streams its tail;
   the wake projection also consumes a stream. Neither path retains all historical
   ciphertexts or all historical snapshots. Earlier runbook descriptions of a
   full-file reader were stale.

   Remaining memory includes the largest record/current snapshot, runtime state,
   kernel indexes (including wallet history and decision choices), and requested
   aggregate output. Some of those indexes still grow with history. Memory-only
   test worlds deliberately retain ciphertexts and are not a production sizing
   model. Measure a disk-backed world and concurrent wake/backup work on the
   actual host; streaming alone is not a guarantee of indefinite capacity.

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

Ubuntu 24.04 requires an AppArmor profile for bubblewrap's namespace setup.
The provisioner installs `bwrap-userns-restrict` when that profile is absent,
then loads it before the service-user jail probe. The vendored profile is unchanged
from [AppArmor 4.0 commit 72229df83059480f4e9fb1488624201bdbd61755](https://gitlab.com/apparmor/apparmor/-/blob/72229df83059480f4e9fb1488624201bdbd61755/profiles/apparmor/profiles/extras/bwrap-userns-restrict),
SHA-256 `a964037f6cf0df1099f14226b037eaedde6237c86e715188e93eb460b30be859`.
It allows bubblewrap's setup and stacks a capability-denying profile on its
children; the runtime still applies its network/process seccomp filter and
filesystem isolation. The global user-namespace restriction remains enabled.
This follows [Ubuntu's targeted bubblewrap guidance](https://discourse.ubuntu.com/t/understanding-apparmor-user-namespace-restriction/58007).

For process-crash tests on slower disks, `FACTORYLAB_TEST_CHILD_TIMEOUT` can
increase the test subprocess watchdog from its default 180 seconds. For example,
`FACTORYLAB_TEST_CHILD_TIMEOUT=1800 uv run pytest -m slow -o addopts="" tests/runtime/test_resume.py`
retains the exact crash points, replay comparisons and conservation assertions.
This variable affects tests only. Run expensive disk-writing suites sequentially;
record any initial watchdog failure and the rerun's actual duration.

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
`{"world":"funded","event":"failed_resume","reason":"manifest_mismatch"}`. The
reason is one code from the closed vocabulary below, or `none`; it gets no
exception text, summary, ledger path, balances, key or model content. HTTPS is
required; redirects are not followed. Alert delivery is best effort (20-second
timeout); failure cannot revive a terminated world. systemd loads the env file;
scripts never source arbitrary shell from it. Test the receiver before launch
using a synthetic payload, and configure its own retention and uptime before the
covenant begins.

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

### Selling a service (edition 2, contract C11)

A population program registered as a tool can be put up for sale with a
`service` proposal (`program_id`, `price_micro`, `description`). Registration
costs one novelty trial, freezes the program's source in a `service.registered`
ledger item, and makes `POST /service/<program_id>` a paid endpoint under x402:
an unpaid request gets the v2 quote (exact canonical Base USDC, the manifest's
`treasury.reserve_address` as `payTo`, the price as the amount); a paid request
carries the buyer's signed EIP-3009 authorization, which `world/seller.py`
verifies by rebuilding exactly the typed data `world/x402.py`'s buyer signs and
recovering its signer, then hands to the facilitator (`FACTORYLAB_FACILITATOR_URL`,
default `https://x402.org/facilitator`) for settlement. Only an explicit,
matching settlement runs the program, in the same jail population tools use, and
returns its output with a `PAYMENT-RESPONSE` header. `GET /services` lists the
catalogue (id, description, price, version, argument schema; never source).

The runtime holds the ledger's only writer lock, so the server runs beside it as
the `factory` user and reads the sealed ledger the way the wake does:

```sh
FACTORYLAB_FACILITATOR_URL=https://x402.org/facilitator \
/srv/factorylab/repo/.venv/bin/python /srv/factorylab/repo/deploy/serve.py \
    --ledger /srv/factorylab/runs/funded.jsonl \
    --spool /srv/factorylab/runs/funded.income.jsonl --bind 127.0.0.1 --port 8402
```

Each settled call is appended to the receipt spool before the program runs, and
the runtime (started with `FACTORYLAB_INCOME_SPOOL=/srv/factorylab/runs/funded.income.jsonl`
in its environment) books every complete receipt line on its next tick as an
`income.earned {service, micro, tx, payer, program, version}` ledger item, through
the recovery journal, with the consumed offset in the treasury snapshot so no
receipt is booked twice. A paid call served in-process (tests, or a future loop
hook) books through `Treasury.earn` directly. The pots view gains three classes:
`earned_micro` (x402 income), `subsidy_micro` (the first complete observation of
the architect's compute credit, ledgered once as `treasury.subsidy`) and
`converted_from_principal_micro` (Venice tranches confirmed from trading capital).
Crediting the earning seat's entitlement is C10's job and lands with it. Publish
the port through a reverse proxy of your own choosing; the server itself never
reads a key, never signs, and exits 2 when the manifest names no reserve address.

### The one control: kill

`systemctl stop` is not a kill. It leaves the world unterminated, the seal
unreleased and the diary unreadable, and the next start resumes it. Ending the
experiment is one command, and it is the only intervention the architect keeps:

```sh
systemctl stop factorylab.service
sudo -u factory /srv/factorylab/repo/.venv/bin/factorylab kill \
    --world funded --ledger /srv/factorylab/runs/funded.jsonl
```

It takes the writer lock (so stop the unit first, or it exits 4), records
`explicit_kill:operator` in the world's own diary through the only authority
that may publish a `Terminated` event, releases the seal and exits 3. Killing an
already dead world prints `terminated` and exits 3 again, changing nothing.
It takes no other argument: there is nothing to steer, only to end.

Afterwards `systemctl disable --now factorylab.service` stops systemd from
starting a resume that would only exit 3. The diary can then be read with
`postmortem` and `versions`; before the kill, reading `runs/funded.jsonl.key`
breaks the covenant.

### Exit-code contract

Every code the CLI can return, and what a supervisor does with it.

| Exit | Path | Supervisor behavior |
| --- | --- | --- |
| 0 | Resume returns a live summary; any command succeeded | Restart with the same saved budget |
| 1 | Resume fails authentication, replay, credentials or provider setup | Failed-resume webhook; restart with backoff |
| 2 | A refusal: an unsafe key file mode, a changed tick, a top-up after launch, a missing argument | Do not restart; the operator must act |
| 3 | Resume finds an authenticated Terminated event, the world terminates while running, or `kill` ends it | Final success; no restart; termination webhook |
| 4 | Another process already holds the ledger's writer lock | Do not start a second writer; investigate |
| 5 | Authenticated startup evidence contains no Launch: no world exists yet | `start.sh` runs the world, then resumes |

`factorylab --help` prints this table too, so an operator on the box has it
without this file.

### Reason codes

Every failing command prints exactly one line, `factorylab <command>: <code>`,
where the code comes from the closed vocabulary in `factorylab/runtime/reasons.py`
(`ledger_integrity`, `manifest_mismatch`, `credential_missing` for a key the
environment lacks, `credential_unsafe` for a key file whose mode or owner is
wrong, `venue_unreachable`, `jail_unavailable`, and the rest). No exception
text, no interpolation and no provider response body is ever printed, so nothing
that could carry a key, an address or a body can reach a log or a webhook.
`run` and `kill` add a second line, `factorylab <command>: raised <Class> in
factorylab.<module>`, because a launch that cannot construct otherwise says only
`adapter_unavailable` and the operator cannot tell which subsystem refused. A
class name and a module path are written in this repository, never by a provider;
no message is read. Supervisors classify on the first line, which never changes.
The same
code is written to `$RUNTIME_DIRECTORY/reason` (mode 0600, cleared at every
start) and `alert.sh` puts it in the webhook body:

```json
{"world": "funded", "event": "failed_resume", "reason": "manifest_mismatch"}
```

A code the webhook cannot match against that vocabulary is reported as `none`.

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
`invocations_by_assembly`, `action_frequencies`, `settlement_latency`, `roster`,
`tools`, `observations`, `charter`, `compute`, `pots`, `immune`, `portfolio`,
`money`, `deliveries`, `commitments`, `cells`, `liveness`,
`world`, `manifest_hash`, `uptime_ns`, `last_event_time_ns`, plus `venue` when a
venue key is present and `reserve` when a reserve key is present. The five views originate
from `Ledger.aggregate`, each verifying the same frozen chain. The three identity-bearing
views (`spend_by_capability`, `invocations_by_assembly`, `action_frequencies`) are projected
to role totals (`producer`, `evaluator`, `meta`, `antagonist`, `noop`, `other`).
Registered assemblies join their declared role; unknown identities join `other`.
In these five views no assembly ids, model bindings, positions or entry prices are
published. Incomplete input or
verification failure retries once after 100 ms; a second failure replaces all
ledger-derived fields with `"unavailable"` and exits 1. Optional account failures
mark only their unavailable fields. There is no exception text in the artifacts.
Each output file is atomically replaced; the pair is not a transactional bundle.

#### The observatory sections (A17, widened)

The rule is one sentence: whatever the population can see is public to the
experimenter; whatever the essay keeps private stays sealed until death. An
assembly reads the world block on every request, so the world block's standing
facts are public by construction. The runtime projects them into one
`wake.public` ledger item at each price-window close (`runtime/pricing.py`,
`_close_price_window`, built by `wake.public_window_item`); the wake republishes
the most recent one. Nothing in that item comes from an external call, so it adds
no journaled I/O and no resumable state. The histories come from public items
that already existed; the wake reads them in the same single authenticated pass
that builds the five views. Every list is capped at the most recent 200 rows so
the page cannot grow with the diary.

| section | reads |
| --- | --- |
| `roster` | `current` and `over_time` from `wake.public.roster` (one row per assembly kind × model id, with counts); `registered` from `Registered` events; `retired` from `actor.retire`. Registration and retirement rows carry a timestamp and what kind of thing joined or left, never an id. |
| `tools` | `wake.public.tools`: the world block's tool specs, with the registry contract version (seed tools are version 1). Id, description and version only — never a tool's source. |
| `observations` | `wake.public.observations`: the measurement catalogue's id, description and units. |
| `charter` | `wake.public.charter`: edition, norms, and every card's observation, region, `lambda` and `answers_for`. `amendments` is assembled from `charter.propose` (with `predicted_effect` and the cards added, replaced or removed), `charter.approved`, `charter.activate`, and the refusals `charter.refused`, `policy.refused` and `amendment.rejected` with their public reason. |
| `compute` | `wallet.commit` items whose reason names a model, bucketed by UTC day and ISO week of the item timestamp; the rail follows the model id namespace exactly as registration routes it (`venice:` → Venice, `x402:` → an x402 seller, otherwise OpenRouter). `invocations_by_kind_per_day` counts `invocation` items by role. |
| `pots` | `wake.public.pots` (venue, reserve, Venice credit, OpenRouter seed, completeness) and every `treasury.submitted`, `treasury.confirmed` and `treasury.refused` with its direction, amount and public refusal reason. |
| `immune` | `immune.window` flags per window, plus the organ's responses: `immune.gain` (direction and pathology only — the exploration rate itself is learner state), `immune.price_relief`, `immune.decay` and `novelty.grant`. |
| `portfolio` | `wake.public.portfolio`: `equity_micro` (the venue pot as last observed, the same number the population reads in `world.pots`), `realized_to_date_micro`, and open positions as coin and side only — no size, no entry price, no lot, no handle. |
| `pots.income` | `wake.public.income`: `earned_micro`, `subsidy_micro` and `converted_from_principal_micro`, the three classes the treasury keeps beside the pots (edition 2). |
| `money` | `in_by_class`: `wallet.initial`, `wallet.drip`, an endowment `wallet.release` whose reason is `release`, `treasury.subsidy`, `income.earned`, `treasury.confirmed` tranches to Venice (`converted_from_principal`), and positive `wallet.settle` by source; `out_by_class`: `wallet.commit` amounts by the reason's class (`model`, `tool`, `connector`, `treasury`, `registration`, `other`), negative `wallet.settle` by source, and confirmed transfer fees. Integer micro-USD, never an address. |
| `deliveries` | `decision.settle`, `decision.timeout` and `ForecastSettled` counted per closed price window (the window a settlement lands in, by `price.window` closes), as rows of channel and status counts. |
| `commitments` | Open decisions (`decision.open` without a `decision.settle` or `decision.timeout`) and sealed forecasts (`forecast.seal` without a `ForecastSettled`): counts, the oldest age, and up to 200 rows of channel or predicate, age and deadline. Ages are against wall time while the world lives and against the last event once it does not. No handle is published. |
| `cells` | The behavioural cell of each `immune.window` profile, computed by the versioning module's `cell_series` with the manifest's immune cuts: dimensions, cuts, one row per window with its cell and whether it changed, and the transition count. |
| `liveness` | `alive`, `dormant` or `terminated`, from the `Launch` and `Terminated` events and the `dormant` items an endowed world writes when it pauses between releases (`entered`/`exited` with timestamps); every dormant period is listed. |

Sealed and never published anywhere in either artifact: learner state and router
weights, propensities, private memories, raw request and return text, per-decision
scores, prompts, tool source, assembly ids and wallet or chain addresses. The page
stays static: no script, no external resource, no address, no key material. These
sections are absent (`"unavailable"`) only when the chain fails to verify; before
the first window closes the standing sections fall back to the genesis manifest's
roster and empty histories.

The snapshot adapter is isolated in `runtime/wake.py` and couples to Ledger's
private read state because its public reopen API is a writer-only recovery API.
It reads the existing adjacent key as the unattended process, never exposes it,
and only inspects event timestamps for timing and authenticated role declarations
for the aggregate projection. Aggregates are not reimplemented
outside the kernel. The genesis must match a manifest in the pinned checkout's
`worlds/`; an unknown or command-line-modified manifest reports unavailable.

Live uptime means elapsed time since the first recorded Tick, includes downtime,
and stops at the terminal event. Before the first tick it is zero. Scripted
uptime and event time are simulated nanoseconds from launch. Event time is the
last recorded event's timestamp, not file mtime or last wallet movement.

Venue fields are `equity_micro` and `realized_to_date_micro`, both integer micro-USD. `realized_to_date_micro` is gross closed trading P&L
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
