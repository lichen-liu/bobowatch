"use strict";
(() => {
  const ranges = typeof module !== "undefined" && module.exports
    ? require("./ranges.js") : globalThis.BoboWatchRanges;
  const scopeKeys = ["project", "site", "host", "replica"];
  const choices = {
    window: new Set([...ranges.rolling, ...ranges.calendar, "custom", "all"]),
    detail: new Set(["auto", "1", "1000", "15000", "60000", "300000", "3600000", "86400000"]),
    refresh: new Set(["0", "30", "60", "300"]),
    traffic: new Set(["rate", "count"]),
    errors: new Set(["rate", "count"]),
    heatmap: new Set(["count", "share"]),
    serviceSort: new Set(["requests", "errors", "error-rate", "mean", "p95", "slow"]),
    routeSort: new Set(["requests", "errors", "error-rate", "mean", "work"]),
    routeMinimum: new Set(["0", "1", "10", "100", "1000"]),
    routeSize: new Set(["10", "25", "100", "all"]),
    eventSeverity: new Set(["", "info", "warning", "error"]),
    eventSize: new Set(["25", "100", "all"]),
  };
  const textLimits = {routeQuery: 128, eventQuery: 256};
  const known = new Set([...scopeKeys, ...Object.keys(choices), ...Object.keys(textLimits), "start_ms", "end_ms", "v"]);
  const id = /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$/;
  const plainText = value => !/[\u0000-\u001f\u007f\uD800-\uDFFF]/u.test(value);
  function bounds(start, end, now) {
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || start >= end ||
        end > 8640000000000000 || end > now + 30000)
      throw new RangeError("Choose valid start and end dates, ending no later than now.");
    return {start_ms: start, end_ms: end};
  }
  function decode(search, {now = Date.now()} = {}) {
    const params = new URLSearchParams(search);
    if (![...params].length) return {present: false, valid: true, state: {}, warnings: []};
    const warnings = [];
    try { decodeURIComponent(search instanceof URLSearchParams ? search.toString() : String(search ?? "")); }
    catch { warnings.push("This dashboard link contains malformed URL encoding."); }
    if (params.has("v") && params.get("v") !== "1")
      warnings.push("This dashboard link uses an unsupported version.");
    const state = Object.fromEntries(scopeKeys.map(key => [key, ""]));
    if ([...params.keys()].some(key => !known.has(key)))
      warnings.push("This dashboard link contains unsupported parameters. Remove them before applying it.");
    for (const key of known) {
      if (!params.has(key)) continue;
      if (params.getAll(key).length !== 1) {
        warnings.push(`The dashboard link repeats ${key}.`);
        continue;
      }
      const value = params.get(key);
      if (scopeKeys.includes(key)) {
        if (value && !id.test(value)) warnings.push(`The dashboard link has an invalid ${key} identifier.`);
        else state[key] = value;
      } else if (choices[key]) {
        if (!choices[key].has(value)) warnings.push(`The dashboard link has an invalid ${key} setting.`);
        else state[key] = value;
      } else if (key in textLimits) {
        if (value.length > textLimits[key] || !plainText(value))
          warnings.push(`The dashboard link has an invalid ${key} filter.`);
        else state[key] = value;
      }
    }
    const hasStart = params.has("start_ms"), hasEnd = params.has("end_ms");
    if (hasStart || hasEnd || state.window === "custom") {
      try {
        if (!hasStart || !hasEnd || !/^\d+$/.test(params.get("start_ms")) || !/^\d+$/.test(params.get("end_ms")) ||
            (state.window && state.window !== "custom")) throw new RangeError();
        state.custom = bounds(Number(params.get("start_ms")), Number(params.get("end_ms")), now);
        state.window = "custom";
      } catch {
        warnings.push("The dashboard link has invalid or conflicting date boundaries.");
      }
    }
    return {present: true, valid: !warnings.length, state: warnings.length ? null : state, warnings};
  }
  function encode(view, {fixedRange = null, now = Date.now()} = {}) {
    const selected = {...view};
    if (fixedRange) {
      selected.custom = bounds(fixedRange.range_start_ms, fixedRange.range_end_ms, now);
      selected.window = "custom";
    }
    for (const key of Object.keys(textLimits)) {
      if (selected[key] !== undefined && selected[key] !== null && !plainText(String(selected[key])))
        throw new RangeError(`Choose a valid ${key} filter.`);
    }
    const params = new URLSearchParams({v: "1"});
    for (const key of [...scopeKeys, ...Object.keys(choices), ...Object.keys(textLimits)]) {
      if (selected[key] !== undefined && selected[key] !== null && (selected[key] !== "" || !scopeKeys.includes(key)))
        params.set(key, String(selected[key]));
    }
    if (selected.window === "custom") {
      const range = bounds(selected.custom?.start_ms, selected.custom?.end_ms, now);
      params.set("start_ms", String(range.start_ms));
      params.set("end_ms", String(range.end_ms));
    }
    const checked = decode(params, {now});
    if (!checked.valid) throw new RangeError(checked.warnings.join(" "));
    return params.toString();
  }
  function merge(preferences, decoded) {
    if (!decoded.valid) throw new RangeError(decoded.warnings.join(" "));
    return {...preferences, ...(decoded.present ? decoded.state : {})};
  }
  function shiftRange(range, direction, now = Date.now()) {
    if (direction !== -1 && direction !== 1) throw new RangeError("Choose the previous or next period.");
    const current = bounds(range.range_start_ms, range.range_end_ms, now);
    const width = current.end_ms - current.start_ms;
    return bounds(current.start_ms + direction * width, current.end_ms + direction * width, now);
  }
  const api = {decode, encode, merge, shiftRange};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else globalThis.BoboWatchWorkspace = api;
})();
