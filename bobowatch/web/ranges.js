"use strict";
(() => {
  const rolling = new Set([
    "30", "60", "120", "300", "600", "900", "1800", "2700",
    "3600", "7200", "10800", "14400", "21600", "28800", "43200", "86400",
    "172800", "259200", "604800", "1209600", "2592000", "5184000", "7776000", "15552000", "31536000",
  ]);
  const calendar = new Set([
    "today", "yesterday", "this-week", "last-week", "this-month", "last-month",
    "this-year", "last-year",
  ]);
  function validateBounds(start, end, now) {
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || start >= end)
      throw new RangeError("Choose a start time before the end time.");
    if (end > now + 30000) throw new RangeError("The end time cannot be in the future.");
    return { start_ms: start, end_ms: end, absolute: true };
  }
  function resolve(preset, now = Date.now(), custom = null) {
    if (preset === "all") return {window: "all", absolute: false};
    if (rolling.has(preset)) return {
      start_ms: now - Number(preset) * 1000, end_ms: now,
      window: Number(preset), absolute: false,
    };
    if (preset === "custom") {
      if (!custom) throw new RangeError("Choose start and end dates, then apply the range.");
      return validateBounds(custom.start_ms, custom.end_ms, now);
    }
    if (!calendar.has(preset)) throw new RangeError("Choose a valid time range.");
    const date = new Date(now);
    const year = date.getFullYear(), month = date.getMonth(), day = date.getDate();
    const midnight = new Date(year, month, day).getTime();
    const monday = new Date(year, month, day - (date.getDay() + 6) % 7);
    let start, end = now;
    switch (preset) {
      case "today": start = midnight; break;
      case "yesterday": start = new Date(year, month, day - 1).getTime(); end = midnight; break;
      case "this-week": start = monday.getTime(); break;
      case "last-week":
        start = new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() - 7).getTime();
        end = monday.getTime(); break;
      case "this-month": start = new Date(year, month, 1).getTime(); break;
      case "last-month": start = new Date(year, month - 1, 1).getTime(); end = new Date(year, month, 1).getTime(); break;
      case "this-year": start = new Date(year, 0, 1).getTime(); break;
      case "last-year": start = new Date(year - 1, 0, 1).getTime(); end = new Date(year, 0, 1).getTime(); break;
    }
    // A calendar period at its exact opening boundary is empty until the next millisecond.
    return validateBounds(start, Math.max(start + 1, end), now);
  }
  function toLocalInput(ms) {
    const date = new Date(ms), pad = value => String(value).padStart(2, "0");
    const seconds = date.getSeconds() ? `:${pad(date.getSeconds())}` : "";
    return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}${seconds}`;
  }
  function parseLocalInput(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value);
    if (!match) throw new RangeError("Choose a valid date and time for each boundary.");
    const parts = match.slice(1).map(value => Number(value || 0));
    const [year, month, day, hour, minute, second] = parts;
    const date = new Date(year, month - 1, day, hour, minute, second);
    if (year < 1970 || date.getFullYear() !== year || date.getMonth() !== month - 1 ||
        date.getDate() !== day || date.getHours() !== hour || date.getMinutes() !== minute ||
        date.getSeconds() !== second)
      throw new RangeError("That date or local time does not exist. Check daylight-saving changes.");
    return date.getTime();
  }
  const api = { rolling, calendar, resolve, toLocalInput, parseLocalInput };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else globalThis.BoboWatchRanges = api;
})();
