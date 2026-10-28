# Reading the dashboard

Choose a project, site or host in the filter bar. **Focus site** and a replica's
name narrow the same overview; the scope line names the selection. **Clear scope**
returns to all projects without changing the time range. On phones, the section
links near the top jump to charts, endpoints, replicas and events.

The time window selects the observations; auto-refresh controls when the browser
fetches them again. Both default to five minutes. Rolling windows move with each
refresh. Calendar presets use the browser's timezone, with Monday-start weeks.
Custom ranges use the native calendar/time controls and stay fixed after Apply,
including during auto-refresh or reload. The end is excluded. The time range,
refresh interval and traffic/error chart modes are remembered in the browser;
credentials remain in tab memory only. Hidden tabs pause polling.

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
Ten matching rows appear per page. The site link focuses its site's overview.

Rows are grouped by site and registered route template, and show request count,
5xx count/rate, mean time and total response time. Total response time includes
overlapping requests; it measures aggregate request duration, not CPU time.
There are no per-route latency percentiles in the current producer protocol.

To keep the response bounded, the server returns the union of the top 20
endpoints for each of the five rankings, at most 100 distinct site/route pairs.
This keeps quiet failing or slow endpoints available for investigation even
when they are absent from the traffic leaders. If other endpoints are omitted,
the explorer says how many candidates were returned. Search and minimum counts
apply to those candidates; narrow the site's scope or time window to find more.

## Coverage and retained history

**Reporting** describes fresh monitoring observations. It does not establish
public uptime or test a website from outside the host. Historical views show
replicas with recorded observations in the selected range. Stale or missing
replicas, clock skew and partial intervals are identified separately. Failed
refreshes preserve the last successful view and mark updates unavailable.

The measurement strip reports status/endpoint coverage and body-size coverage.
Old v1 producers can still supply request counts, server errors and latency
without extended HTTP measurements. Their missing data is not filled with zero.

Queries include all retained observations in the selected scope and range. Larger
ranges aggregate counters and histograms in SQL, keeping roughly 180 chart buckets
without discarding early observations. Request/error totals, latency distributions,
body measurements and endpoint rankings cover the same range. Live gauges still
use the latest actual sample, rather than a bucket average.

A two-second query budget protects ingestion and memory. A query that exceeds it
returns a timeout with no partial result; the dashboard keeps the previous range
and asks you to narrow the scope or dates. If a saved range times out on first
load, the range controls remain available. Installed history lives on the quota
limited RAM disk. Earlier data may have been evicted or lost on reboot. Selecting
a longer range cannot restore it. **History & storage** shows usage, quota, epoch,
oldest retained observation and eviction counts.

These views use existing SQL observations. They add no producer fields, storage
migration, external service, frontend build or runtime dependency. Updating
BoboWatch alone enables them for existing HTTP integrations.
