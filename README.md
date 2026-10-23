# BoboWatch

A small, standalone monitoring tool for multiple projects, sites and replicas.
One Python process, a private dashboard and a local SQL database.

**The HTTP dashboard works:** a fresh demo has two projects, four replicas and
30 minutes of explicitly generated history. Six time-series/distribution panels
show request rate, p50/p95/p99 latency bounds, 4xx/5xx errors, active requests,
body throughput and duration buckets, with endpoint and replica comparisons.
No real applications have been integrated.

**This is a prototype, not a production-ready monitoring installation.**

**Operation is manual and on demand, including future production use.** Start
`demo` or `serve` when you want to monitor, and stop it with Ctrl-C when finished.
No launchd/systemd service, login auto-start or automatic restart is needed.
Stored SQL history remains available next time; new observations, checks and
alerts are not collected or evaluated while the monitoring process is stopped.

![BoboWatch dashboard showing fictional activity](docs/images/dashboard.png)

## Start the demo

Requires Python 3.11+ on macOS or Linux. Run from this checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-core.txt
.venv/bin/python -m bobowatch demo
```

Open [http://127.0.0.1:8720](http://127.0.0.1:8720). The dashboard asks for the
admin token in `~/.local/state/bobowatch-demo/admin.token`; read that local file
and paste its contents into the login form. Tokens stay in the browser tab's
memory, not URLs or browser storage.

The demo generates fictional requests and events for two projects. A fresh
runtime first seeds 30 minutes of fictional observations into SQL, then receives
live synthetic traffic. Existing history is never backfilled or overwritten. It contacts
only its own loopback ingestion API. Stop it with Ctrl-C. Run the same command
again to continue using the existing database and credentials; new producer runs
appear under the same replica identities.

For another port or runtime location, use a separate directory:

```sh
.venv/bin/python -m bobowatch demo --state-dir /tmp/bobowatch-demo --port 8721
```

For a fresh review with the richer HTTP history, use a new demo runtime:

```sh
.venv/bin/python -m bobowatch demo --state-dir "$HOME/.local/state/bobowatch-demo-http"
```

Existing runtime configuration is never overwritten. Edit `monitor.toml` to
change an existing runtime's port. Keep data, tokens and backups outside the
source checkout. Generated secrets have mode 0600 and new runtime directories
have mode 0700.

## What is implemented

- TOML inventory for arbitrary projects, sites, hosts and expected replicas.
- A dependency-free `Monitor` adapter with explicit request/event hooks, bounded
  in-memory state and background HTTP delivery. Request recording does no
  telemetry disk or network I/O.
- Per-replica producer credentials and a separate admin credential.
- Versioned JSON snapshots, monotonic process counters, fixed latency histograms,
  immutable run identity, sequence deduplication and atomic event acceptance.
- A bounded single SQL writer, versioned/checksummed migration, WAL storage,
  foreign keys and a lock preventing two monitoring processes on the same database.
- Dashboard project/site/host/time filters, replica drill-down, roughly 180 chart
  buckets per selected window, hover/keyboard readouts and togglable chart series.
- Requests per second, p50/p95/p99 bounds, 4xx/5xx shares, in-flight work, body
  throughput, response-class mix and endpoint request/error/mean-duration totals.
- Waiting, stale, clock-skew, partial-observation and disconnected states.
- Consistent online SQLite backups and refusal to overwrite an existing backup.

Only **Waitress** is a core third-party dependency in this milestone. The adapter
uses the standard library; importing it neither starts threads nor imports the
server. The design's second planned dependency, psutil, arrives with host sampling.
There is no frontend build step, CDN, ORM, database server or collector suite.

## Initialize an empty installation

```sh
.venv/bin/python -m bobowatch init
.venv/bin/python -m bobowatch serve --config "$HOME/.local/state/bobowatch/monitor.toml"
```

`init` creates the private runtime configuration, admin token and SQLite schema.
An empty installation shows no sites until inventory is configured. See
[the example inventory](examples/monitor.toml) and
[the implementation notes](docs/implementation.md) for adding a producer.
The default listener is loopback. LAN access is available through the configuration below.

## Open the dashboard from a phone or another local computer

In your private runtime's `monitor.toml`, change the listener and optionally allow
an exact local DNS hostname:

```toml
[monitor]
listen = "0.0.0.0:8720"
allowed_hosts = ["bobowatch.local"] # optional; use your computer's actual hostname
```

Keep the other existing settings, then restart BoboWatch manually. Open
`http://<this-computer-LAN-IP>:8720` from a device on the same network and use the
same admin token. `127.0.0.1` on your phone points to the phone itself.

The LAN listener accepts local IPv4 clients and private/link-local IP Host headers
plus explicitly configured hostnames. Arbitrary Host headers, public source IPs
and cross-origin API requests remain blocked. Authentication is unchanged.
Use this HTTP mode on a trusted LAN; it does not encrypt token traffic. No router
port forwarding or public deployment is required.

## Back up history

This command also works while the server is running:

```sh
.venv/bin/python -m bobowatch backup \
  --config "$HOME/.local/state/bobowatch-demo/monitor.toml" \
  --output /path/to/new-backup.sqlite3
```

It uses SQLite's backup API, then checks integrity and foreign keys. Store the
runtime configuration and token files separately alongside your recovery plan.
Restore to a new runtime directory with its configuration/secrets and a copy of
the backup named by `database_url`; do not overwrite a running database.

## GitHub repository

Source: [lichen-liu/bobowatch](https://github.com/lichen-liu/bobowatch). See
[repository setup](docs/github.md) and the [CI and branch policy](.github/README.md)
for contributing and requesting checks.

## Checks

The suite uses Python's standard-library `unittest`. Core requirements are enough:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Optional development tools:

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/ruff check bobowatch tests examples
.venv/bin/ruff format --check bobowatch tests examples
```

The tests cover protocol/authentication boundaries, atomic retries and event
conflicts, counter resets, missing data, migration checks, inventory retirement,
backup restoration and a real HTTP process-restart/online-backup exercise.

## Instrument HTTP serving explicitly

Register bounded route templates and wrap actual request work with the adapter's
explicit lifecycle hook:

```python
from bobowatch import Monitor

monitor = Monitor(
    replica="web-r01", endpoint="http://127.0.0.1:8720/v1/ingest",
    token_file="/private/runtime/web.token", routes=("/api/search",),
)
monitor.start()
with monitor.request("/api/search") as response:
    # Perform request work, then set the actual status and encoded body sizes.
    response.status = 200
    response.bytes_sent = len(encoded_response)
    response.bytes_received = len(request_body)
```

The context counts one completion and clears its in-flight gauge on exceptions.
An uncaught exception records 500. Body throughput requires both body sizes;
uninstrumented size/concurrency values remain unknown rather than zero.
`record_request` also accepts a registered `route`, `bytes_sent` and
`bytes_received` for explicit completion hooks. Unknown route names collapse into
`unmatched`; raw paths and query strings do not become new dimensions.

New adapters emit protocol v2. The core still accepts v1 producers; extended
metrics remain unavailable for those samples. Existing v1 SQL databases migrate
in place with an automatic consistent pre-migration backup. Start a new producer
run when upgrading its wire version; upgrade the core before its adapters.

## Next milestones

The [runtime and RAM-storage design](docs/runtime-storage-design.md) proposes
`fyi.boboji.watch`, on-demand supervision, a bounded five-GiB RAM disk and local
administration. Those lifecycle/storage features are not implemented yet.

ASGI/WSGI wrappers, real app integrations, process/host sampling, public probes,
job/freshness hooks, alert state, automatic retention and server-database support
are **not implemented yet**. This is a working local pilot, not the complete
monitoring design. History queries are capped at 4,000 snapshots per scope, with
visible truncation; raw storage currently grows until managed by the operator.
One producer process per replica slot is supported. Use separate slots for
concurrent workers until worker grouping is implemented.

PostgreSQL support requires a tested backend and a data migration. An unsupported
database URL fails at startup rather than silently choosing SQLite.

- [Implemented architecture and protocol](docs/implementation.md)
- [Longer-term design](docs/design.md)
- [Original illustrative schema](docs/schema.sql)
- [Initial migration](bobowatch/migrations/001.sql) and [HTTP extension](bobowatch/migrations/002.sql)
- [Original proposal and dashboard study](https://github.com/lichen-liu/flightrepo/pull/25)
