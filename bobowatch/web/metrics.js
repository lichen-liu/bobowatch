"use strict";
(() => {
  const sorts = {
    requests: r => r.requests,
    errors: r => r.errors,
    "error-rate": r => r.requests ? r.errors / r.requests : -Infinity,
    mean: r => r.requests ? r.duration_us / r.requests : -Infinity,
    work: r => r.duration_us,
  };
  function paginate(rows, {page = 0, size = 10} = {}) {
    const length = size === "all" ? Math.max(1, rows.length)
      : Number.isInteger(Number(size)) && Number(size) > 0 ? Number(size) : 10;
    const pages = Math.max(1, Math.ceil(rows.length / length));
    const current = Math.max(0, Math.min(pages - 1, Math.floor(page) || 0));
    const offset = current * length;
    return {rows: rows.slice(offset, offset + length), total: rows.length,
      page: current, pages, first: rows.length ? offset + 1 : 0,
      last: Math.min(offset + length, rows.length)};
  }
  function endpoints(routes, { query = "", sort = "requests", minimum = 0, page = 0, size = 10 } = {}) {
    const needle = query.trim().toLowerCase();
    const score = sorts[sort] || sorts.requests;
    const matches = routes.filter(r => r.requests >= minimum &&
      `${r.route} ${r.site}`.toLowerCase().includes(needle));
    matches.sort((a, b) => score(b) - score(a) || b.requests - a.requests ||
      a.site.localeCompare(b.site) || a.route.localeCompare(b.route));
    return paginate(matches, {page, size});
  }
  function events(records, {query = "", severity = "", page = 0, size = 25} = {}) {
    const needle = query.trim().toLowerCase();
    return paginate(records.filter(row => (!severity || row.severity === severity) &&
      `${row.message} ${row.site} ${row.replica}`.toLowerCase().includes(needle)), {page, size});
  }
  function nearestIndex(points, at) {
    if (!points.length) return -1;
    let left = 0, right = points.length;
    while (left < right) {
      const middle = Math.floor((left + right) / 2);
      if (points[middle].at_ms < at) left = middle + 1;
      else right = middle;
    }
    if (!left) return 0;
    if (left === points.length) return left - 1;
    return at - points[left - 1].at_ms <= points[left].at_ms - at ? left - 1 : left;
  }
  function endpointContributions(routes) {
    // Denominators describe every instrumented endpoint in the selected scope,
    // before the table's search, minimum-request filter or pagination.
    return routes.reduce((total, route) => {
      total.requests += route.requests;
      total.errors += route.errors;
      total.duration_us += route.duration_us;
      return total;
    }, {requests: 0, errors: 0, duration_us: 0});
  }
  function latencyHeatmap(points, {start, end, columns, bucket_ms = 1}) {
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start >= end ||
        !Number.isSafeInteger(columns) || columns < 1 ||
        !Number.isSafeInteger(bucket_ms) || bucket_ms < 1)
      throw new RangeError("Use an increasing millisecond range and positive column/bucket sizes.");
    // Every display boundary is an epoch-aligned multiple of the source bucket,
    // so a grouped API observation never gets split into invented empty cells.
    // Offset edges can add one column beyond the requested presentation target.
    const step = Math.ceil((end - start) / columns / bucket_ms) * bucket_ms;
    const epoch = Math.floor(start / step) * step;
    const count = Math.ceil((end - epoch) / step);
    const result = Array.from({length: count}, (_, index) => {
      const from = Math.max(start, epoch + index * step);
      return {
        at_ms: from,
        start_ms: from,
        end_ms: Math.min(end, epoch + (index + 1) * step),
        buckets: Array(14).fill(0),
        requests: 0,
        quality: {snapshots: 0, partial: 0, skewed: 0, extended_http: 0},
        partial: false,
        observed: false,
        source_points: 0,
      };
    });
    for (const point of points) {
      if (point.at_ms < start || point.at_ms > end) continue;
      if (!Number.isSafeInteger(point.at_ms) || !Array.isArray(point.duration_buckets) ||
          point.duration_buckets.length !== 14)
        throw new TypeError("Each latency point needs a millisecond timestamp and 14 duration buckets.");
      // Rolling API ranges include their exact end timestamp; absolute ranges
      // already exclude it in SQL. Keep a closing observation in the final cell.
      const index = point.at_ms === end ? count - 1
        : Math.floor((point.at_ms - epoch) / step);
      const column = result[index];
      for (let bucket = 0; bucket < 14; bucket++) {
        column.buckets[bucket] += point.duration_buckets[bucket];
        column.requests += point.duration_buckets[bucket];
      }
      for (const key of Object.keys(column.quality))
        column.quality[key] += point.quality?.[key] ?? 0;
      // Source flags can overlap; point.partial additionally describes missing
      // expected fleet members, so zero source flags do not establish coverage.
      column.partial ||= Boolean(point.partial) || (point.quality?.partial ?? 0) > 0 ||
        (point.quality?.skewed ?? 0) > 0;
      column.observed = true;
      column.source_points++;
    }
    return result;
  }
  function compareSites(sites, sort = "requests") {
    const requests = site => site.http?.requests ?? site.requests ?? 0;
    const scores = {
      requests,
      errors: site => site.http?.errors ?? site.errors ?? 0,
      "error-rate": site => requests(site)
        ? (site.http?.errors ?? site.errors ?? 0) / requests(site) : -Infinity,
      mean: site => site.http?.mean_ms ?? -Infinity,
      p95: site => site.http?.latency?.p95?.overflow ? Infinity
        : site.http?.latency?.p95?.upper_ms ?? -Infinity,
      slow: site => site.http?.slow_request_pct ?? -Infinity,
    };
    const score = scores[sort] || scores.requests;
    return [...sites].sort((a, b) => {
      const left = score(a), right = score(b);
      return (left === right ? 0 : right > left ? 1 : -1) ||
        requests(b) - requests(a) || a.id.localeCompare(b.id);
    });
  }
  const api = { endpoints, events, nearestIndex, endpointContributions, latencyHeatmap, compareSites };
  globalThis.BoboWatchMetrics = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
