# Install and operate BoboWatch on macOS

Use Python 3.11+ as your normal macOS user, without sudo. The only application
runtime dependency is pinned Waitress; the adapter is standard-library Python.
The installer requires internet access for its virtual environment dependency.
Run the following commands from the BoboWatch checkout after merging the runtime PR.

## Install, then start when wanted

```sh
cd ~/p/bobowatch
git switch main
git pull --ff-only
python3 installation/install.py
./deployment/watch start
./deployment/watch check
./deployment/watch status
```

Installation writes a private source snapshot and virtual environment to
`~/Library/Application Support/fyi.boboji.watch`. It does not mount a RAM disk,
load a job, start a server or change login/boot settings. Start allocates and
verifies the RAM volume on first use; it can fail if memory headroom is too low.
The default is five GiB, including filesystem space for the whole SQLite family.

`start` explicitly loads a private launchd session with crash restart and a
10-second restart throttle. `stop` unloads it, waits for the server to exit and
leaves the RAM volume mounted. There is no login/boot auto-start. A new login or
reboot requires another explicit start. An alternative is foreground mode:

```sh
./deployment/watch serve
```

Stop foreground mode with Ctrl-C in its terminal. Foreground mode has no crash
restart. Do not run both modes for the same runtime. `stop` refuses to kill a
foreground process; it tells you to use its terminal.

## Open the dashboard

Successful `deploy` prints a **Dashboard (this Mac)** link and detected
**Dashboard (local network, interface)** links, using the configured port.
`start`, `check` and `status` include the same links in their JSON `dashboard`
field; foreground `serve` prints them before serving. For a stopped runtime,
`status` still shows the configured URLs alongside `running: false`.

Open `http://127.0.0.1:8720` on the serving Mac. Installed runtimes open the
read-only dashboard directly, without a token or login, for local-network clients.
The first installation/update creates a private `dashboard-policy.json` selecting
`local` access if no explicit policy exists. Existing policy choices survive
reinstallation. Any device on the trusted local network can view the dashboard.

Producer ingestion still requires each app's scoped credential. Storage and
configuration control still require the admin credential and loopback access;
the local admin CLI reads that credential automatically from its private file.
Viewing the dashboard grants no write/control access.

To explicitly change the dashboard policy, including while serving:

```sh
./deployment/watch admin dashboard access token
./deployment/watch admin dashboard access local
```

Changes reload immediately through the local control API, or at the next start
when configured while stopped. Reload a browser after enabling local access.
Switching back to token mode asks active local viewers to log in on their next
refresh. Legacy `init`/`demo` runtimes keep token login unless explicitly changed.
For token mode only, `./deployment/watch token` displays your private admin secret;
paste it into the login form. Keep that output out of shared terminal logs.

The installed listener defaults to `0.0.0.0:8720`. From a phone on the same trusted
LAN, use `http://<Mac-LAN-IP>:8720`, not `127.0.0.1`. For the usual Wi-Fi interface,
`ipconfig getifaddr en0` shows the address. Other network interfaces may differ.
Detected addresses are local interface candidates, not a remote reachability
check. Choose the Wi-Fi/Ethernet interface shared with your phone; VM/VPN
interfaces may also appear. Discovery never contacts an external service. If
address discovery fails, commands still succeed and show the local URL plus a
LAN-address hint. A loopback-only listener explicitly reports LAN access disabled.
Allow incoming Python connections if macOS asks. Local hostname access requires
that exact hostname in `monitor.toml`'s `allowed_hosts`; private IPv4 addresses
work directly. Local dashboard mode is intended for a trusted LAN. HTTP is
unencrypted; public source addresses, unknown hosts and cross-origin reads remain
blocked. No router port forwarding is needed.

The dashboard defaults to **Last 5 minutes** with **Auto-refresh: Every 5 minutes**.
The time window also offers **Last 1 minute** and **Last 30 seconds**, alongside
longer history windows. Auto-refresh is a separate setting with **Every 30 seconds**,
**Every 1 minute**, **Every 5 minutes** and **Off**. Your time-window and refresh
choices are remembered in this browser; credentials are never stored with them.
Manual refresh and filter changes fetch immediately. Hidden tabs pause automatic
updates and refresh once when visible again, unless auto-refresh is off. Requests
never overlap, and a failed update retains the previous data with a warning and
retries at the selected interval. Short-window chart labels show seconds; producer
sampling still determines the number of observations available. Charts use the
configured sampling cadence to identify gaps and show isolated observations as dots.

The dashboard is empty until a real app is registered and sends snapshots.
Installed operation never seeds fictional data. Legacy `python -m bobowatch demo`
remains a separate disk-backed review tool; it is not the installed runtime.

## Calendar and custom history ranges

The time-window menu separates rolling windows from calendar periods:

| Preset | Range |
| --- | --- |
| Last 24 hours / 7 days / 30 days / 90 days | Exact elapsed duration ending at refresh time |
| Today / Yesterday | Local midnight to now / the previous complete local day |
| This week / Last week | Monday 00:00 to now / the previous complete Monday–Monday week |
| This month / Last month | First of the month to now / the previous complete calendar month |
| This year / Last year | January 1 to now / the previous complete calendar year |
| Custom range | Explicit start and end date/time selected with native calendar controls |

The browser's local timezone is shown below the controls. Calendar days follow
local daylight-saving changes, so Yesterday may span 23 or 25 hours; Last 24 hours
always spans 24 elapsed hours. Weeks start on Monday. For custom ranges, select
both dates/times and click **Apply range**. The start is included and the end is
excluded; select the next day's midnight to include a complete final day. Empty,
reversed, nonexistent local times and future ranges are rejected. Ambiguous
fall-back times use the earlier offset, matching the browser's date handling.

Presets and applied custom boundaries are remembered across reloads. Auto-refresh
advances rolling/current-calendar ranges; a custom range stays fixed until you
apply new boundaries. Unapplied edits do not change the displayed range. Five
minutes remains the initial default. Historical/calendar views show request and
observation totals in place of current live gauges; chart axes/tooltips include
dates for longer spans.

Only retained RAM history can be displayed. Selecting last month does not create
missing records or recover history lost on reboot. The existing 4,000-snapshot
query cap and two-second read budget still apply; the dashboard identifies capped,
empty or partially retained results rather than claiming a complete period.

The overview API accepts either a supported `window` in seconds or both
`start_ms` and `end_ms` as UTC Unix-millisecond integers, alongside existing scope
filters. Absolute ranges are half-open and return their exact bounds. A small
30-second future tolerance accommodates browser/server clock skew; it does not
create future observations. No SQL migration or new runtime dependency is needed.

## One-command deployment

```sh
cd ~/p/bobowatch
./deployment/watch deploy
```

This explicitly stops the supervised session, installs the current checkout,
starts it and performs an authenticated storage check. It also works for a new
installation. It preserves configuration, registrations, tokens, quota policy
and surviving RAM history; it never releases/reformats an existing owned volume.
Optional `--runtime-dir`, `--ram-size` and `--port` match the installer. Capacity
changes still require an explicit stopped release; deploy never discards history
for you. Installation alone continues to start nothing.

A failed stop prevents installation. A failed installation stays stopped; a failed
start/final check unloads the session. Cleanup failures are reported explicitly.
No automatic rollback or login/boot startup is added. A foreground process must
first be stopped in its terminal. Update the checkout separately with `git pull`.

## Reinstall or update

```sh
cd ~/p/bobowatch
./deployment/watch stop
git switch main
git pull --ff-only
python3 installation/install.py
./deployment/watch start
./deployment/watch check
```

Reinstallation preserves configuration, registrations, credentials, quota policy
and any surviving RAM history. It refuses while a server/session is active. A
failed installation leaves an incomplete marker; rerun the installer while
stopped before starting. Installed source hashes are verified on start; modifying
the checkout alone does not change an installed process. Quota policy,
registration and token edits are reloaded without reinstalling.

Legacy disk/demo runtimes are not automatically converted. The installer refuses
one at the selected path rather than silently importing/deleting its history.
If a previous demo used `fyi.boboji.watch`, stop that demo, keep its entire directory
as a separately named backup, then install into the now-unused default path.

## Storage and local admin

```sh
./deployment/watch admin
./deployment/watch admin storage status
./deployment/watch admin storage quota 5GiB
./deployment/watch admin registrations list
./deployment/watch admin config reload
```

The interactive prompt also supports `storage prune`, `dashboard access [local|token]`, `help` and `quit`. Commands
while running use the authenticated loopback control API and the server's single
SQL writer. Stopped-runtime inspection never creates a database. Persistent quota
changes while stopped apply at the next start. No command changes Flightrepo's
ingestion disk quota or accesses its database.

The five-GiB default is `5 * 1024**3` bytes. Retention starts deleting oldest
server-received history when live pages approach 60% of the requested quota;
checkpoints start at 70% physical usage. A main-file page ceiling at 80%, admission
reserves and the dedicated RAM-volume capacity protect space for WAL, SHM,
filesystem overhead and maintenance. SQLite temporary query work stays in memory.
The writer checks capacity before new ingestion and runs maintenance on idle ticks.
Requests that cannot preserve reserve get retryable HTTP 503, never a false ack.
A held reader can temporarily prevent reclamation; requests resume after it exits.

Status reports main/WAL/SHM bytes, live/reusable pages, volume free space, quota,
`quota_applied`, history epoch, oldest/newest retained observations and eviction
counts. A decrease can be pending while files shrink; do not interpret the new
requested quota as already applied until `quota_applied` is true. The small
protected inventory/current baselines may prevent impractically small quotas.
A quota increase is live only up to the allocated RAM volume size.

Normal stop/start preserves history only while the RAM volume survives. Reboot,
power loss or explicit release can erase it. There are no automatic durable
snapshots or fallback database. A fresh database has a new epoch, shown in the UI;
the first producer snapshot establishes a baseline rather than counting all
pre-epoch traffic. Later snapshots count deltas normally. Historical rows are
bounded by capacity, not a promised number of days. Query results retain their
existing 4,000-snapshot cap and visible truncation.

A bounded receipt cache retains the most recent 256 event IDs per replica in
addition to retained events. Event deduplication beyond both horizons is not
promised. Superseded runs are rejected using each replica's retained latest run;
retention protects its current snapshot baseline and removes obsolete runs.

To deliberately discard history and free the RAM allocation:

```sh
./deployment/watch stop
./deployment/watch release --yes
```

To replace the allocated volume with a different capacity (history is discarded):

```sh
./deployment/watch stop
./deployment/watch release --yes
python3 installation/install.py --ram-size 6GiB
./deployment/watch admin storage quota 6GiB
./deployment/watch start
```

There is no unlimited RAM mode. New allocations must pass the startup memory
headroom check. Changing the volume size alone preserves the prior quota up to
the new size; set the desired quota explicitly. The OS may swap RAM; a RAM disk
is not a guarantee that telemetry never reaches swap storage.

## Paths and configuration updates

| File/location | Purpose |
| --- | --- |
| `monitor.toml` | Listener, admin credential location and static inventory |
| `registrations.json` | Atomically provisioned dynamic app inventory |
| `storage-policy.json` | Persistent requested quota and RAM capacity |
| `dashboard-policy.json` | Local-network dashboard access or token login |
| `tokens/` | Private admin and registered-producer credentials |
| `app/`, `venv/`, `installation.json`, `manage.py` | Installed snapshot, dependency and receipt |
| `jobs/` | Session definition; never copied into LaunchAgents |
| `logs/watch.log` and three rotated files | Diagnostics, at most approximately 40 MiB total |
| `ramdisk.json` | Owned mount/device/session metadata, no history |
| `/Volumes/BoboWatch-<runtime-hash>/data/monitor.sqlite3` | Verified RAM-only history with adjacent WAL/SHM |

Path selection is `--runtime-dir`, then `BOBOWATCH_RUNTIME_DIR`, then the default
`fyi.boboji.watch` directory. An explicit option must be used on both installation
and every launcher invocation when selecting another runtime, for example:

```sh
python3 installation/install.py --runtime-dir "$HOME/Library/Application Support/watch-test" --port 8721
./deployment/watch start --runtime-dir "$HOME/Library/Application Support/watch-test"
```

The server reloads valid inventory, credential and quota changes every two seconds.
Use `config reload` to request an immediate reload. Invalid changes keep the prior
valid state. Listener, database path and RAM mode changes require a stopped restart.
`logs/watch.log` records rejected reloads. If a verified mount disappears or is
replaced, SQL access fails closed; it never creates a normal-disk replacement.

## Flightrepo rollout

Install BoboWatch before the first monitoring-enabled Flightrepo start. It may be
running or stopped during startup registration. Flightrepo installation itself
needs no BoboWatch runtime or API. For an existing Flightrepo installation, after
updating its startup-registration controller:

```sh
cd ~/p/flightrepo
git switch main
git pull --ff-only
git submodule update --init vendor/bobowatch
./deployment/flyji deploy \
  --bobowatch-runtime-dir "$HOME/Library/Application Support/fyi.boboji.watch"
./deployment/flyji status
./deployment/flyji check
```

That single deployment command stops the serving jobs, builds/installs the wheel,
registers/reuses the scoped producer token at startup, starts Flightrepo and its
existing tunnel, then checks local operation. It requires the existing tunnel token; a
first-time Flightrepo installation must complete its separate tunnel setup.
It never starts BoboWatch and does not restart the independent ingestion worker.
Later deployments reuse the saved monitoring runtime and registration even when
the flag is omitted. If you prefer separate steps, stop Flightrepo, run
`python3 installation/install.py`, then run
`./deployment/flyji start --bobowatch-runtime-dir PATH` and
`./deployment/flyji check`.

Both apps retain their own private configuration. Flightrepo's
`~/Library/Application Support/fyi.boboji.fly/settings.json` records BoboWatch's
runtime location for provisioning plus an ingestion URL and its own producer
token-file path for delivery. Requests go to `/v1/ingest` over HTTP on loopback;
Flightrepo never shares or opens the monitoring SQLite file. If BoboWatch is down,
Flightrepo continues serving with bounded in-memory telemetry retry. No durable
client queue is added.

Start BoboWatch, use Flightrepo normally, wait about 15 seconds and refresh the
dashboard. Its first received snapshot is the new-epoch baseline, so exercise the
app again and wait another interval to see interval counts. Monitoring covers ASGI
HTTP requests; host CPU/memory, public uptime probes, ingestion progress, freshness
and alerts are future work. This is a tested local monitoring pilot, not a complete
production monitoring/alerting system.


To switch an already-enabled Flightrepo sender to another local monitoring
runtime, use `./deployment/flyji monitoring --bobowatch-runtime-dir PATH` from the
Flightrepo checkout. It saves the target for later starts and deployments; no
reinstall is required. Enabling monitoring that was disabled at process startup
still needs a restart. Endpoint-only manual settings without a local registration
record are not re-provisioned at startup.


For development, calendar and daylight-saving unit tests use Node's built-in test
runner (CI's runner already provides Node):

```sh
node --test tests/test_calendar_ranges.cjs
```

Node is not required to install or serve BoboWatch.
