"use strict";
(() => {
  const sorts = {
    requests: r => r.requests,
    errors: r => r.errors,
    "error-rate": r => r.errors / r.requests,
    mean: r => r.duration_us / r.requests,
    work: r => r.duration_us,
  };
  function endpoints(routes, { query = "", sort = "requests", minimum = 0, page = 0 } = {}) {
    const needle = query.trim().toLowerCase();
    const score = sorts[sort] || sorts.requests;
    const matches = routes.filter(r => r.requests > 0 && r.requests >= minimum &&
      `${r.route} ${r.site}`.toLowerCase().includes(needle));
    matches.sort((a, b) => score(b) - score(a) || b.requests - a.requests ||
      a.site.localeCompare(b.site) || a.route.localeCompare(b.route));
    const pages = Math.max(1, Math.ceil(matches.length / 10));
    const current = Math.max(0, Math.min(pages - 1, Math.floor(page) || 0));
    return { rows: matches.slice(current * 10, current * 10 + 10),
      total: matches.length, page: current, pages };
  }
  const api = { endpoints };
  globalThis.BoboWatchMetrics = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
