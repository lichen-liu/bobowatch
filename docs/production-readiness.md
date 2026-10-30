# Production readiness for an internal monitoring dashboard

BoboWatch targets an explicitly started, private LAN monitoring workspace for
multiple small applications. This review covers the code at the diagnostics
iteration following PR #12. It defines the next work in terms of observable
behavior and acceptance checks; it is not a claim that unimplemented signals
already exist.

## Product and architecture boundaries

- One Python process, SQLite and static HTML/CSS/JavaScript; Waitress remains the
  only core third-party dependency. Browser testing dependencies are development
  tools and are never installed into an application or the installed runtime.
- The installed runtime retains history on a five-GiB RAM volume. Circular
  retention removes old history. Reboot, power loss and explicit volume release
  can discard it. Credentials and inventory stay outside that volume.
- Dashboard reads need no login on the trusted LAN. Producer credentials and
  loopback administrator controls remain separate. Read-only diagnostics do not
  grant quota, inventory or credential mutations.
- All matching retained observations, routes and events contribute to queries.
  There are no query row caps, duration caps or browser query deadlines. Source
  detail and complete JSON exports remain available. Grouping and pagination
  affect presentation, never the queried totals.
- Monitoring reporting, observed request success and public availability are
  different signals. Neither a reporting producer nor a zero-error interval
  establishes public uptime. No-data percentages and gauges remain unknown.

## Delivered in this iteration

| Capability | Behavior and validation |
| --- | --- |
| Honest live values | The browser advances server time using elapsed monotonic time. Reporting states and current traffic/in-flight gauges expire between queries, even with five-minute refresh or refresh Off. Historical totals stay intact. |
| Explicit query lifecycle | A visible Cancel control stops waiting in the browser. A new requested view supersedes the old request; an obsolete response cannot overwrite it. The displayed range and scope remain attached to the last successful data. Cancellation does not promise that SQLite work stopped. |
| Recoverable connection | In-page Retry handles an initial connection failure. Offline state suppresses live gauges; returning online retries the connection. Hidden tabs pause automatic network polling. |
| Reproducible investigations | URLs encode validated scope, time/detail and table/presentation controls. Copy view link freezes the successfully queried dates and scope. Back/Forward restores views. Tokens never enter generated links. Invalid links require an explicit reset instead of broadening a query. |
| Time navigation | Previous/next moves by exactly the displayed elapsed duration, including millisecond boundaries, and creates a custom range. Event actions inspect the surrounding five minutes on each side. Return to last five minutes restores a rolling range. |
| Monitor self-health | A dedicated read-only panel separates liveness, storage readiness, writer/queue state, maintenance, configuration reload rejection and ingestion HTTP outcomes. Last-check dates remain visible. |
| Storage visibility | Quota use, reserve headroom, physical database/WAL/shared-memory sizes, free volume, oldest/newest retained metric dates, cumulative evictions and history epoch are visible. An epoch change is surfaced. No speculative retention forecast is shown. |
| Scoped history extent | Snapshot and event extents are calculated for the selected replica scope. Another site's older history no longer hides a selected site's shorter retained extent. Extents do not assert uninterrupted coverage. |
| Telemetry diagnostics | Exact last-observed/received timestamps and revision are inspectable per selected replica. Received-minus-observed is labeled a clock delta because producer clock offset contributes to it. |
| Consistent graph input | Line charts support touch tap, accurate SVG coordinate mapping, Home/End and arrows, persistent textual readouts and focus preservation through rendering. Unknown, partial, sparse and measured-zero data remain distinct. |
| Browser regression checks | Fictional data exercises desktop Chromium and phone WebKit, navigation, slow/failing requests, freshness, accessible charts and complete data. CI browser dependencies are isolated from runtime dependencies. |

## Prioritized opportunities

The following opportunities are intentionally not folded into an unbounded
single dashboard change. Each row identifies the data or architecture needed
and a concrete completion criterion.

| Priority | Opportunity | Why it matters / completion criterion |
| --- | --- | --- |
| Next | Isolate ingestion from long analytical reads | Four shared Waitress workers can all be occupied by complete-history reads. Separate ingress execution or use a small asynchronous query executor without truncating history. Under concurrent source-detail reads, producer acknowledgements must remain timely and every accepted sequence must remain deduplicated. |
| Next | Capacity tests with concurrent exports | Large results are currently materialized in server/browser memory; response spooling shares the RAM volume. Measure peak RAM, spool/free volume, ingestion acknowledgement and recovery with a near-quota database and several complete exports. Keep the exact query contract. |
| Next | Query performance and responsive rendering | Report query/transfer/render duration separately, and investigate SQL plans before adding indexes. Yield expensive rendering or move presentation work to a browser worker. Input and Cancel must remain responsive with hundreds of thousands of source timestamps, without losing exported records. |
| Next | Previous-period comparison | Offer an explicit adjacent baseline with visible dates. Compare count, weighted mean, 5xx percentage-point change and summed histogram bands; never average percentile values. Distinguish zero baseline, missing data, new sites and unequal retained extents. |
| Next | Deployment and process timeline | Existing run IDs, starts and revisions can identify process starts and revision changes without a producer upgrade. Show markers and event links on a shared timeline. A restart is not automatically a deployment, and correlation is not causation. |
| Next | Cross-chart investigation | Synchronized cursors, accessible interval selection, zoom/reset and expandable panels would connect spikes across traffic, latency and errors. Always show the exact proposed dates before applying a new range; preserve keyboard and phone access. |
| Next | Explicit objectives | Optional site-level request-success and latency objectives can use existing counts and exact histogram boundaries. Show sample count, coverage and objective definition. Label them observed request performance, not external availability. |
| Next | More usable information density | Add comfortable/compact density, collapsible chart groups and saved named views only after measuring readability on phones and large fleets. Preserve section navigation, clear scope and visibility of active filters. |
| Next | Accessible textual alternatives | Persistent chart readouts cover individual observations. A browsable exact-value table per metric and printable/exportable incident summary would serve nonvisual review and sharing. It must remain complete and disclose grouping. |
| Next | Empty-state onboarding | Distinguish empty inventory, waiting producer, retired-only scope, empty historical dates and a changed RAM epoch. Link to the relevant integration/operator documentation and show a safe next command without exposing tokens. |
| Protocol | Optional host/process sampling | CPU, RSS, disk, network, open descriptors and process uptime need a deliberately optional sampler and versioned SQL/protocol fields. Do not silently substitute machine-wide metrics for application-process metrics. Test macOS and Linux behavior and sampler cost. |
| Protocol | Application gauges and job freshness | DB health, ingestion progress, last successful job and freshness age need named, bounded measurements with units and lifecycle semantics. Unknown and stale must remain distinct from zero. Keep hooks importable without starting threads. |
| Protocol | Per-endpoint distributions | Current endpoint totals cannot reconstruct p95/p99, response-class distributions or a route time series at finer detail. A versioned extension should add bounded route histograms and optional method dimensions; validate cardinality, conservation and migration cost. |
| Protocol | More exact HTTP outcomes | Status classes cannot identify 401/403/404/429/502/503 individually. Add a bounded representation for useful exact codes; distinguish completed, aborted and disconnected requests and explain middleware visibility. |
| Protocol | Producer delivery health | Dropped events and delivery failures currently remain adapter-local. Send cumulative, deduplicated diagnostics after recovery, without putting network or disk I/O on request recording. Explicitly disclose loss instead of inferring it from gaps. |
| Optional | Public and internal probes | Independent HTTP/DNS/TLS checks can establish a defined perspective on availability and certificate expiry. Keep targets explicit, bounded and optional; distinguish the probe's failure from an application failure. Respect on-demand operation. |
| Optional | Local alert evaluation | Start with visible, deduplicated state transitions and a testable rule model. Require sustained failure, unknown handling, recovery and clock tests. Outbound notifications should be configured explicitly; never depend on a large collector stack. |
| Optional | SQL server backend | A backend interface alone is not migration support. Add tested PostgreSQL or other SQL transactions, schema migrations and a verified transfer path before documenting it as supported. Preserve idempotency, compatible histogram aggregation and consistent reads. |
| Optional | Remote-host ingestion | Current producers use loopback HTTP and intentionally reject unencrypted remote HTTP. Introduce an explicit TLS/tunnel configuration and certificate validation before supporting cross-host delivery. Keep local setup simple. |
| Maintenance | Quiet-scope SQL indexes | Scoped event extrema can scan unrelated events. Profile realistic multi-site event volumes; add a compatible migration/index only when measured plans demonstrate benefit. |
| Maintenance | Queue latency diagnostics | Current readiness sees full queues and long active writes. Measure oldest queued work and acknowledgement latency to expose sustained waiting behind many shorter writes. Readiness remains a current diagnostic, not an acceptance guarantee. |
| Maintenance | Accessible contrast and target audit | Check final computed colors, focus states, zoom, reduced motion, screen-reader labels and touch targets, including all newly added states. Browser assertions alone do not replace manual assistive-technology review. |
| Maintenance | Reproducible deployment record | Retain an inspectable installed revision and schema version; verify readiness and asset identity after upgrades. Exercise interrupted installs and clean rollback without discarding the RAM history or credentials. |

## Verification and rollout gates

Changes should keep the existing Python and pure Node checks, then run the
development-only browser suite against isolated fictional data. The meaningful
regressions are cross-layer: delayed old responses, cancellation, hidden/offline
tabs, expired gauges with refresh Off, clock changes, malformed/missing scope
links, phone touch coordinates, unknown versus zero, quota pressure and epoch
changes. Complete queried counts and histogram bins must survive search,
pagination, chart grouping and exported views.

Review the PR and exact-head CI before merging. Deploy with the existing
`./deployment/watch deploy`, then verify installed revision/assets, readiness,
LAN access, quota, history epoch and oldest retained observation. Verify that
credentials and inventory are unchanged. No application adapter redeployment is
required for this iteration. Production observations should be inspected in
memory during checks, without creating screenshots or exported data files.

The current shared-worker contention and large-export capacity questions remain
the most important operational acceptance work before expanding the fleet. The
existing local pilot can gain the delivered usability improvements independently;
it should not be represented as an already complete external monitoring service.
