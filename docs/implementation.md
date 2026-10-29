# First milestone: implementation notes

This describes v0.3. The longer-term proposal in `design.md` includes capabilities
that are not part of this implementation.

## On-demand operation

Installed macOS operation uses the [runtime manager](runtime.md): installation
starts nothing; an explicit `start` enables a crash-restarted session, `stop`
unloads it, and `serve` runs in the foreground. No login/boot auto-start. Installed
history lives only on its verified RAM volume. Stop/start preserves it while that
volume survives; a lost/released volume starts a new epoch with a producer
baseline. Legacy development `demo`/`init` retain their disk-backed behavior.

While stopped the core collects nothing. Adapters continue recording cumulative
counters in bounded memory, independently of core availability. Surviving runs can
recover counts after gaps within an existing epoch, but cannot reconstruct timing.
App restarts can lose unsent observations and events.

## Boundaries

`bobowatch.adapter.Monitor` and `protocol.py` use only the standard library.
They are importable directly from a pinned submodule checkout. Construct and
start a Monitor inside the serving process after fork. There is no package
installation requirement for the adapter and no framework monkeypatching.

`application.py` exposes a small WSGI application served by Waitress. It validates
credentials, origins, host headers, request sizes and protocol values before
submitting ingestion to the backend. Bundled browser assets use no external
services. The service defaults to loopback HTTP. Setting `listen = "0.0.0.0:8720"` enables
trusted-LAN access from local IPv4 clients, with private IP Host headers and
optional exact `allowed_hosts` names. Admin tokens and same-origin checks remain
required. LAN HTTP is unencrypted; use an encrypted transport for other networks.

`storage.Storage` is the boundary used by the HTTP API. `SQLiteStore` owns
SQLite-specific parameter binding, migrations, indexes, backups and conflict
handling. It has a bounded queue and one writer connection; dashboard reads use
independent read transactions without query deadlines. The domain protocol has
no SQL dialect or database credentials. No second backend is implemented.

All snapshot state, cumulative-to-interval deltas, histogram buckets and events
commit in one transaction. Success is acknowledged only after that commit.
A timed-out request might still commit, so a retry must retain the same sequence
and contents. Duplicate snapshots/events never count twice. A conflict rejects
the entire transaction. Retries do not refresh liveness.

Migrations are `../bobowatch/migrations/001.sql` through `003.sql`. Version 2 adds
relational HTTP gauges, status-class counters and bounded route totals; version 1
is left unchanged. Version 3 adds storage epoch/eviction state, receipt-order indexes and bounded
event acknowledgements. Legacy disk databases receive a consistent pre-upgrade
backup; installed RAM history is never automatically backed up. It is different from
the more extensive illustrative schema in `schema.sql`; editing an applied
migration causes a checksum failure. Future changes need a new ordered migration.

## Inventory and credentials

`init` creates an empty runtime; `demo` creates a separate runtime with two
synthetic sites. Both keep credentials and SQL outside the checkout. Inventory loads at startup and is revalidated every two seconds; invalid reloads
keep prior settings. Listener/database/demo changes require restart. Invalid inventory fails startup.

To configure a new replica in a real empty runtime:

1. Run `init`, then add project, host, site and replica entries following
   `../examples/monitor.toml`.
2. Generate a distinct random producer token in a private file. For example,
   from a shell with `umask 077`, run
   `python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > web.token`
   inside the private runtime directory. Reference its filename from inventory.
3. Start `serve` with that runtime's `monitor.toml`. Give that replica's adapter
   only its own token file, never the admin token.
4. Use `../examples/record_requests.py` to exercise the explicit API before
   connecting it to real request handling.

Stable site/project/host/replica IDs preserve joins and history. A restart uses a
new run UUID under the same replica. Concurrent processes need distinct replica
slots in v0.1. A slot cannot move to a new site/host without a new ID. To remove an
expected target, keep its inventory entry with `retired = true`; removing a known
replica entry outright fails startup. Old SQL history remains in either case,
although the current dashboard lists only active inventory.

Tokens are scoped and their hashes are persisted for producer registration; raw
tokens live in runtime files. Installed dashboard reads are available without
login to local-network clients. Producer ingestion remains authenticated, and
administrator control additionally requires loopback access. The separate private
`dashboard-policy.json` can select token mode; only then does the browser hold
the admin token in tab memory. The policy reloads live, and explicit choices survive
reinstallation. Legacy `init`/`demo` runtimes default to token mode. The API disables cross-origin access and the UI inserts event text using
`textContent`. Callers must supply redacted operational summaries: automatic
redaction or unrestricted log collection is not implemented.

## Wire contract

`POST /v1/ingest` uses `Authorization: Bearer <producer token>` and a JSON body
limited to 256 KiB. Both versions share the following base fields; v2 adds the HTTP object below:

| Field | Meaning |
| --- | --- |
| `version` | Integer `1` or `2` |
| `replica` | Inventory replica ID bound to this credential |
| `run_id`, `revision`, `started_at_ms` | Immutable process UUID, revision and start time |
| `sequence` | Increasing integer within the run |
| `observed_at_ms`, `elapsed_ms` | UTC observation time and monotonic process elapsed time |
| `requests`, `errors`, `duration_sum_us` | Cumulative integer request count, 5xx count and duration sum |
| `buckets` | Fourteen noncumulative histogram counts whose sum equals `requests` |
| `events` | At most 100 entries with stable ID, UTC `at_ms`, severity and redacted message |

Bucket upper bounds are 5, 10, 25, 50, 100, 250 and 500 ms; 1, 2.5, 5, 10, 30 and
120 seconds; then overflow. Percentiles display a conservative bucket upper
bound, with an observation count. They are computed from summed compatible
histogram deltas, not averages of replica percentiles.

Responses: `200` committed/duplicate, `400` invalid, `401` missing/wrong credential,
`409` conflicting sequence/event/run, `413` oversized, `503` unavailable/busy SQL.
Producer credentials cannot read the admin API and admin credentials cannot
submit producer telemetry. `GET /health` is unauthenticated process liveness,
not database readiness or site availability.

`GET /api/overview` requires the admin credential unless trusted-LAN reads are
enabled. Optional filters are `project`, `site`, `host`, `replica`, and a supported
rolling `window`, or absolute `start_ms`/`end_ms` boundaries.
The response contains inventory, replica freshness, historical aggregates, bounded
chart bins and recent events. Counts select intervals ending within the window;
edge intervals may overlap it. The UI states this rather than implying exact
arbitrary-window counts. All retained matching observations are included, with
no query row cap, top-endpoint selection, event cutoff or execution deadline.
SQL aggregates counters, histograms and status classes by replica, timestamp group
and producer version. Automatic chart detail produces roughly 180 groups; optional
positive `bucket_ms` selects a fixed width (1 means every source timestamp).
Grouping changes presentation, never the selected observations or totals.
Observation counts describe source snapshots, not aggregate rows. All endpoints
are returned for combined, site and replica scopes, including idle templates.
Events are complete for the selected range. Retired inventory/history remains
queryable; retired replicas are excluded from live gauges and expected counts.

`window=all` derives half-open boundaries from all retained matching snapshots and events.
Other positive integer windows have no duration maximum and start no earlier than
the Unix epoch. Absolute bounds remain exact. Live gauges use the latest actual
interval, with deterministic ordering for equal timestamps across producer restarts.
Existing time/run indexes are reused without a migration.

Overview reads have no progress-handler deadline, retry database lock contention
without a fixed timeout, and read storage metadata in the same transaction rather
than through the writer acknowledgement queue. The browser has no automatic
query-abort timer. It shows progress, retains the previous successful view on a
real failure, and keeps range controls usable. Pagination and search operate on
complete endpoint/event arrays; All rows and a complete JSON view download are
available. No new runtime dependency or producer protocol is required.

## Failure and history semantics

The request path updates in-memory counters only. A background sender uses one
in-flight request, a two-second timeout and bounded backoff. It replaces obsolete
pending metric snapshots with current cumulative values. While the process stays
alive, later snapshots recover totals after a gap; their exact timing remains
unknown. Overlong intervals are marked partial.

Crashes can lose increments since the last accepted snapshot. The event ring is
bounded at 100 entries; overflow increments `Monitor.dropped_events`. That counter
is inspectable locally but is not yet exported into SQL. The adapter truncates
operational summaries to 256 characters to bound snapshot size. `close()` waits
for the sender within a deadline and does not promise a final delivery.

Freshness depends on both receipt and observation time; stale payloads and retries
cannot keep a replica green. Clock skew over 30 seconds is displayed separately.
Missing observations are not zero requests, and telemetry does not establish
public availability. Colocated monitoring cannot detect its own complete outage
while it is down. No notifications are sent.

SQL history, baselines, credentials/inventory hashes and events survive restart.
Automatic pruning, rollups and storage-budget enforcement are future work. Do
not run an unattended high-volume installation before those are added. The demo
is intended for interactive evaluation; Ctrl-C stops generation without deleting
history.


## HTTP protocol v2

The additional `http` object has `in_flight` (nullable sampled gauge), cumulative
integer `bytes_sent`, `bytes_received`, `sized_requests`, five cumulative
`status_classes` (1xx through 5xx) and `routes`. Each route contains its registered
name plus cumulative `requests`, `errors` and `duration_sum_us`. There are at most
20 registered templates plus `unmatched`; the route set is immutable in a run.
Route totals and status classes must reconcile with the base snapshot. Counters
cannot regress; all extended records participate in the same atomic transaction.

The adapter's `request()` context measures elapsed work, records completion and
clears active work in `finally`. Direct `record_request()` calls leave concurrency
unknown unless lifecycle instrumentation is also used. Provide both body sizes
for throughput coverage; the chart does not substitute missing sizes with zero.
The dependency-free ASGI wrapper is now available; WSGI instrumentation remains
future work. See integration.md for setup and the deployment gate.

Request rates are each replica's interval count divided by its monotonic interval,
then summed across replicas. Multiple observations in a chart bucket use a
count/duration ratio per replica. Missing or gap-spanning intervals are not plotted
as zero or bridged. Percentile bounds come from merged compatible histograms.
Top endpoints remain grouped by site and registered route, so identically named
paths from different projects do not lose their origin. Extended metric coverage
is visible for mixed v1/v2 data.

The overview also returns weighted mean latency, counts/shares of requests over
one second, measured body byte totals/means and per-bucket error counts. These
are derived from the existing snapshot deltas without a schema or producer
protocol change. Every recorded endpoint template is returned for each scope;
chart aggregation skips route calculations and uses separately aggregated full
route totals. See [dashboard controls and metric definitions](dashboard.md) for
measurement and retention semantics.

A fresh **demo-only** runtime seeds 30 minutes of generated observations with
fictional observation/receipt times. These are stored and queried in SQL, not
invented in browser chart code. Existing runtimes are not backfilled. The import
path refuses non-demo runtimes, and the HTTP API does not accept fixture timestamp
controls. Demo labels remain visible. Neither seeding nor the live sample workload
starts an OS service or enables auto-start.


## Diagnostic history fields

Overview HTTP summaries include integer `duration_us` and
`quality = {snapshots, partial, skewed, extended_http}`. Chart points additionally
include `duration_buckets`, the same 14 noncumulative bucket counts used by the
whole-window histogram, and the source-level `quality` counts. Existing fields,
including `snapshot_count`, retain their semantics. SQL grouping sums partial and
skewed source flags; taking only a maximum flag per chart group would undercount.
These additive response fields require no producer change or database migration.

The heatmap's client display grouping uses epoch-aligned multiples of the API
bucket size. It combines all compatible source bins and quality counts without
splitting a coarse API group, interpolating missing measurements or changing query
totals. The display's pixel width changes the number of visible columns only;
`Every timestamp` and JSON exports continue to include every returned timestamp.
Endpoint contribution denominators are computed before browser table filtering,
so an isolated row keeps its share of the full instrumented endpoint scope.
