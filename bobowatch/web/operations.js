"use strict";
(function (root) {
  // Advance from server time with elapsed monotonic time, independent of the
  // browser's wall clock. A long query counts towards the age of its response.
  function status(replica, now, staleSeconds) {
    if (replica.retired) return "retired";
    if (replica.observed_at_ms == null || replica.received_at_ms == null) return "waiting";
    if (replica.status === "skewed") return "skewed";
    return Math.max(now - replica.observed_at_ms, now - replica.received_at_ms) > staleSeconds * 1000
      ? "stale" : "fresh";
  }
  function current(value, replicas, now, staleSeconds, interrupted = false) {
    const active = replicas.filter(r => !r.retired);
    return !interrupted && active.length && active.every(r => status(r, now, staleSeconds) === "fresh")
      ? value : {rps: null, in_flight: null};
  }
  function capacity(storage) {
    if (!storage?.available && storage?.available !== undefined) return null;
    if (!storage) return null;
    const used = storage.used_bytes, quota = storage.quota_bytes;
    return {
      used, quota,
      ratio: quota > 0 ? used / quota : null,
      headroom: quota > 0 ? Math.max(0, quota - used - (storage.reserve_bytes || 0)) : null,
      warning: storage.quota_applied === false || quota > 0 && used >= quota * .9,
    };
  }
  const api = {status, current, capacity};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.BoboWatchOperations = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
