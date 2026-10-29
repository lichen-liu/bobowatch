# Reading the dashboard

Choose a project, site or host in the filter bar. **Focus site** and a replica's
name narrow the same overview; the scope line names the selection. **Clear scope**
returns to all projects without changing the time range. On phones, the section
links near the top jump to charts, endpoints, replicas and events.

The clock button opens a searchable time picker. Quick ranges are grouped into
minutes, hours, days/weeks and calendar periods. Search by name or shorthand such
as `2h` or `14d`; press Enter to apply the first match. Rolling presets cover
30 seconds through 365 days, with intermediate 2/4/8-hour, 2/3/14-day and
60/180-day choices. On phones, switch between **Quick ranges** and **Custom range**.
Escape, the close button or a click outside dismisses the picker without applying
date edits. Use **Apply time range** to commit custom dates.

The adjacent chart-detail control changes grouping. The refresh arrow runs a query
now; its attached interval selector shows **30s**, **1m**, **5m** or **Off**.
The download icon exports the complete queried view as JSON.

The time window selects the observations; auto-refresh controls when the browser
fetches them again. Both default to five minutes. Rolling windows move with each
refresh. Calendar presets use the browser's timezone, with Monday-start weeks.
Custom ranges use the native calendar/time controls and stay fixed after Apply,
including during auto-refresh or reload. The end is excluded. The time range,
refresh interval, chart detail and traffic/error chart modes are remembered in the browser;
credentials remain in tab memory only. Hidden tabs pause polling.

**All retained history** spans the oldest through the newest matching metric snapshot or event,
including retained records from retired replicas. Custom dates have no duration
cap; the API also accepts any positive rolling window in seconds. **Chart detail**
offers automatic grouping, fixed time groups and **Every timestamp**. Grouping
combines all observations in a time group; it never samples or drops records.
At source detail, multiple replicas reporting the same timestamp are combined.

## HTTP signals

- **Request rate** sums per-replica interval rates. Switch the traffic chart to
  **Requests / bucket** to see counts clearly on a quiet server. Counts belong
  to the bucket containing an observation's completion timestamp; a long sample
  interval can include requests from before that bucket's beginning.
- **p50, p95 and p99** are conservative histogram upper bounds. **Mean response
  time** divides total recorded duration by completed requests. It is weighted
  by request count, including when combining replicas or chart buckets.
- **Responses over 1s** counts durations strictly greater than one second using
  the histogram's one-second boundary. This is an investigation threshold,
  rather than a configured availability or latency objective.
- **Server errors** counts 5xx responses. **Client errors** counts 4xx among
  requests whose producer supplied status classes. Error charts switch between
  percentages and counts. The coverage strip identifies missing extended data.
- **In flight** is a sampled gauge. A historical/calendar range shows recorded
  totals instead of claiming to show active requests now.
- **Body throughput** requires complete size measurement for the plotted interval.
  **Mean body size** uses only completed requests with both body sizes measured.
  The coverage strip shows their share of all requests and the measured bytes
  sent/received. These are application body bytes; they exclude headers and
  transport overhead. A measured empty body is zero; an unmeasured body is unknown.

Hover a chart or focus it and use the left/right arrows for values. Toggle series
in a chart's legend. Mean latency readouts show measured means; percentile
readouts retain the upper-bound symbol. Gaps and missing measurements remain
unknown. No completed requests means no latency, size average or percentage.

## Investigating endpoints

Use **Explore server errors** or **Explore slow endpoints** from the summary,
or go directly to the endpoint explorer. Search registered route templates or
site IDs, rank by traffic, 5xx count, 5xx rate, mean duration or total response
time, and set a minimum request count to avoid ranking a route on a tiny sample.
All recorded endpoint templates are loaded, including idle templates by default.
Rows offers 10, 25, 100 or All; every matching row remains reachable. Search,
rankings and minimum counts operate on the entire returned set. The site link
focuses its site's overview.

Rows are grouped by site and registered route template, and show request count,
5xx count/rate, mean time and total response time. Total response time includes
overlapping requests; it measures aggregate request duration, not CPU time.
There are no per-route latency percentiles in the current producer protocol.

The events section also loads the complete selected range. Search by message,
site or replica, filter by severity, and choose 25, 100 or All rows. Timestamps
include the local date and time. Pagination affects presentation only.

**Download JSON** saves all totals, chart points, endpoints and events from the
last successful query, regardless of the current table page or search. The file
includes the applied scope, exact dates, chart grouping, and storage/history
metadata. Credentials are never included. Choose Every timestamp before exporting
when you want source time resolution.

## Coverage and retained history

**Reporting** describes fresh monitoring observations. It does not establish
public uptime or test a website from outside the host. Historical views show
replicas with recorded observations in the selected range. Stale or missing
replicas, clock skew and partial intervals are identified separately. Retired
replicas remain available for historical analysis and do not contribute to live
gauges or the expected active-replica count. Observed counts, latency and body
averages remain visible for partial intervals with a tooltip annotation; unknown
fleet rates, concurrency and throughput remain gaps. Failed
refreshes preserve the last successful view and mark updates unavailable.

The measurement strip reports status/endpoint coverage and body-size coverage.
Old v1 producers can still supply request counts, server errors and latency
without extended HTTP measurements. Their missing data is not filled with zero.

Queries include all retained observations in the selected scope and range, with
no snapshot cap, endpoint cap, event cutoff or execution deadline. All query paths
aggregate compatible counters and histograms in SQL. Totals, latency distributions,
body measurements and endpoints cover the same range. Live gauges use the latest
actual sample, rather than a time-group average. Reads and their storage metadata
share one consistent SQL transaction and wait for database locks without a query
time budget. They do not wait on the ingestion writer's acknowledgement queue.

The browser waits for a query to finish and shows elapsed time. Auto-refresh waits
for completion before scheduling the next request. A failed request preserves the
last successful view; range controls remain available on an initial failure.
The view is labeled with its data's as-of time.

Installed history lives on the quota limited RAM disk. Earlier data may have been
evicted or lost on reboot. Selecting a longer range cannot restore it.
**History & storage** shows usage, quota, epoch, oldest retained observation and
eviction counts.

These views use existing SQL observations. They add no producer fields, storage
migration, external service, frontend build or runtime dependency. Updating
BoboWatch alone enables them for existing HTTP integrations.
