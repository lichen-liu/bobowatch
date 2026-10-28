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
  const api = { endpoints, events, nearestIndex };
  globalThis.BoboWatchMetrics = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
