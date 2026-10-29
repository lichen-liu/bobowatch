"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  token: "",
  localAccess: false,
  data: null,
  replica: "",
  busy: false,
  pending: false,
  error: "",
  session: 0,
  controller: null,
  refreshSeconds: 300,
  customRange: null,
  routePage: 0,
  eventPage: 0,
  appliedScope: {},
  hiddenSeries: new Map(),
  loadedMono: 0,
  nextRefresh: null,
  operations: null,
  operationsMono: 0,
  operationsError: "",
  operationsBusy: false,
  epochChanged: false,
  workspaceInvalid: false,
  initialLocation: true,
  appliedView: null,
};
let timer;
let operationsTimer;
const preferenceKey = "bobowatch.dashboard.preferences.v1";
const ranges = BoboWatchRanges;
const timeWindows = new Set([...ranges.rolling, ...ranges.calendar, "custom", "all"]);
const chartDetails = new Set(["auto", "1", "1000", "15000", "60000", "300000", "3600000", "86400000"]);
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "Local time";
const refreshIntervals = new Set(["0", "30", "60", "300"]);
function restorePreferences() {
  try {
    const saved = JSON.parse(localStorage.getItem(preferenceKey));
    if (saved?.custom) {
      try { state.customRange = ranges.resolve("custom", Date.now(), saved.custom); }
      catch { state.customRange = null; }
    }
    if (saved && timeWindows.has(saved.window) && (saved.window !== "custom" || state.customRange))
      $("window").value = saved.window;
    if (saved && refreshIntervals.has(saved.refresh))
      $("auto-refresh").value = saved.refresh;
    if (["rate", "count"].includes(saved?.traffic)) $("traffic-view").value = saved.traffic;
    if (["rate", "count"].includes(saved?.errors)) $("error-view").value = saved.errors;
    if (chartDetails.has(saved?.detail)) $("chart-detail").value = saved.detail;
    if (["count", "share"].includes(saved?.heatmap)) $("heatmap-mode").value = saved.heatmap;
    if (["requests", "errors", "error-rate", "mean", "p95", "slow"].includes(saved?.serviceSort)) $("service-sort").value = saved.serviceSort;
  } catch {
    // Storage may be unavailable or contain invalid data. Keep the defaults.
  }
  state.refreshSeconds = Number($("auto-refresh").value);
  syncTimeControls();
}
function savePreferences() {
  try {
    localStorage.setItem(
      preferenceKey,
      JSON.stringify({
        window: $("window").value,
        refresh: $("auto-refresh").value,
        custom: state.customRange && {
          start_ms: state.customRange.start_ms, end_ms: state.customRange.end_ms,
        },
        traffic: $("traffic-view").value,
        errors: $("error-view").value,
        detail: $("chart-detail").value,
        heatmap: $("heatmap-mode").value,
        serviceSort: $("service-sort").value,
      }),
    );
  } catch {
    // The dashboard still works when browser storage is unavailable.
  }
}
function syncTimeControls() {
  const selected = $("window").selectedOptions[0];
  let label = selected.textContent;
  if (selected.value === "custom" && state.customRange) {
    const stamp = ms => new Date(ms).toLocaleString([], {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
    });
    label = `${stamp(state.customRange.start_ms)} → ${stamp(state.customRange.end_ms)}`;
  }
  $("time-range-label").textContent = label;
  $("time-range-button").setAttribute("aria-label", `Time range: ${label}`);
  $("time-picker-current").textContent = label;
  for (const button of $("time-picker").querySelectorAll("[data-range]"))
    button.setAttribute("aria-pressed", String(button.dataset.range === selected.value));
  const interval = $("auto-refresh");
  interval.title = interval.value === "0" ? "Auto-refresh off" : `Auto-refresh every ${interval.selectedOptions[0].textContent}`;
}
function setPickerView(view) {
  $("time-picker").dataset.view = view;
  $("quick-range-tab").setAttribute("aria-pressed", String(view === "quick"));
  $("custom-range-tab").setAttribute("aria-pressed", String(view === "custom"));
}
function positionTimePicker() {
  const picker = $("time-picker");
  if (!picker.open) return;
  if (matchMedia("(max-width: 640px)").matches) {
    picker.style.removeProperty("left");
    picker.style.removeProperty("top");
    return;
  }
  const button = $("time-range-button").getBoundingClientRect();
  const rect = picker.getBoundingClientRect();
  picker.style.left = `${Math.max(12, Math.min(innerWidth - rect.width - 12, button.left))}px`;
  picker.style.top = `${Math.max(12, Math.min(button.bottom + 8, innerHeight - rect.height - 12))}px`;
}
function filterQuickRanges() {
  const needle = $("range-search").value.toLowerCase().replace(/\s+/g, "");
  const duration = /^(?:last)?(\d+)([smhdw])$/.exec(needle);
  const seconds = duration && Number(duration[1]) * {s: 1, m: 60, h: 3600, d: 86400, w: 604800}[duration[2]];
  let matches = 0;
  for (const group of $("quick-ranges").children) {
    let visible = 0;
    for (const button of group.querySelectorAll("[data-range]")) {
      button.hidden = duration ? Number(button.dataset.range) !== seconds : !button.dataset.search.includes(needle);
      if (!button.hidden) visible++;
    }
    group.hidden = !visible;
    matches += visible;
  }
  $("range-empty").hidden = matches > 0;
}
function closeTimePicker() {
  $("time-picker").close();
  $("time-range-button").setAttribute("aria-expanded", "false");
  $("time-range-button").focus({preventScroll: true});
}
function openTimePicker() {
  const selected = $("window").value;
  let range = selected === "custom" && state.customRange;
  if (!range) {
    range = selected === "all" && state.data
      ? { start_ms: state.data.range_start_ms, end_ms: state.data.range_end_ms }
      : ranges.resolve(selected === "custom" || selected === "all" ? "300" : selected);
  }
  $("range-start").value = ranges.toLocalInput(range.start_ms);
  $("range-end").value = ranges.toLocalInput(range.end_ms);
  $("range-zone").textContent = `${timezone} · End time excluded`;
  $("range-error").hidden = true;
  $("range-search").value = "";
  filterQuickRanges();
  setPickerView(selected === "custom" ? "custom" : "quick");
  syncTimeControls();
  $("time-picker").showModal();
  $("time-range-button").setAttribute("aria-expanded", "true");
  positionTimePicker();
  (selected === "custom" ? $("range-start") : $("range-search")).focus({preventScroll: true});
}
function setupTimePicker() {
  for (const group of $("window").querySelectorAll("optgroup")) {
    const section = document.createElement("section");
    section.className = "quick-range-group";
    section.setAttribute("aria-label", group.label);
    const heading = document.createElement("h3");
    heading.textContent = group.label;
    section.append(heading);
    for (const option of group.children) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = option.textContent;
      button.dataset.range = option.value;
      let search = option.textContent;
      if (ranges.rolling.has(option.value)) {
        const seconds = Number(option.value);
        for (const [unit, scale] of [["s", 1], ["m", 60], ["h", 3600], ["d", 86400], ["w", 604800]])
          if (seconds % scale === 0) search += ` ${seconds / scale}${unit}`;
      }
      button.dataset.search = search.toLowerCase().replace(/\s+/g, "");
      button.setAttribute("aria-pressed", "false");
      section.append(button);
    }
    $("quick-ranges").append(section);
  }
  $("time-range-button").addEventListener("click", openTimePicker);
  $("close-time-picker").addEventListener("click", closeTimePicker);
  $("time-picker").addEventListener("close", () => $("time-range-button").setAttribute("aria-expanded", String($("time-picker").open)));
  $("time-picker").addEventListener("cancel", event => {
    event.preventDefault();
    closeTimePicker();
  });
  $("time-picker").addEventListener("click", event => {
    const button = event.target.closest("[data-range]");
    if (button) {
      $("window").value = button.dataset.range;
      $("window").dispatchEvent(new Event("change"));
      closeTimePicker();
    } else if (event.target === $("time-picker")) {
      const rect = $("time-picker").getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)
        closeTimePicker();
    }
  });
  $("range-search").addEventListener("input", filterQuickRanges);
  for (const id of ["range-start", "range-end"])
    $(id).addEventListener("input", () => { $("range-error").hidden = true; });
  $("range-search").addEventListener("keydown", event => {
    const first = $("quick-ranges").querySelector("section:not([hidden]) button:not([hidden])");
    if (event.key === "Enter") {
      event.preventDefault();
      first?.click();
    } else if (event.key === "ArrowDown" && first) {
      event.preventDefault();
      first.focus();
    }
  });
  $("quick-ranges").addEventListener("keydown", event => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const buttons = [...$("quick-ranges").querySelectorAll("section:not([hidden]) button:not([hidden])")];
    const current = buttons.indexOf(event.target);
    if (current < 0) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1
      : (current + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
    buttons[next].focus();
  });
  for (const [id, view] of [["quick-range-tab", "quick"], ["custom-range-tab", "custom"]])
    $(id).addEventListener("click", () => {
      setPickerView(view);
      (view === "quick" ? $("range-search") : $("range-start")).focus({preventScroll: true});
    });
  addEventListener("resize", positionTimePicker);
}
function rangeStamp(ms) {
  return new Date(ms).toLocaleString([], {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}
function axisClock(ms) {
  const duration = state.data.window_seconds;
  if (duration <= 300) return detailClock(ms);
  if (duration <= 86400) return clock(ms);
  if (duration <= 172800) return new Date(ms).toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  });
  return new Date(ms).toLocaleDateString([], { month: "short", day: "numeric" });
}
function scheduleRefresh() {
  clearTimeout(timer);
  timer = null;
  state.nextRefresh = null;
  if (
    state.busy || document.hidden || !state.refreshSeconds ||
    (!state.token && !state.localAccess)
  ) return;
  state.nextRefresh = performance.now() + state.refreshSeconds * 1000;
  timer = setTimeout(() => {
    timer = null;
    refresh();
  }, state.refreshSeconds * 1000);
}
const colors = {
  teal: "#178b7b",
  blue: "#5286de",
  purple: "#9874d8",
  amber: "#d69a39",
  red: "#da6572",
};
const el = (tag, text, cls) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
};
const append = (parent, ...children) => {
  parent.append(...children);
  return parent;
};
function replaceAllChildren(parent, children) {
  const fragment = document.createDocumentFragment();
  for (const child of children) fragment.append(child);
  parent.replaceChildren(fragment);
  return parent;
}
const numeric = (value, digits = 1) =>
  value === null || value === undefined
    ? "—"
    : value.toLocaleString(undefined, { maximumFractionDigits: digits });
const number = (value) => numeric(value, 0);
const clock = (ms) =>
  new Date(ms).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
const detailClock = (ms) => new Date(ms).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false});
const age = (ms) => {
  if (ms === null) return "Not observed";
  const seconds = Math.max(0, Math.round((Date.now() - ms) / 1000));
  return seconds < 60 ? `${seconds}s ago` : `${Math.floor(seconds / 60)}m ago`;
};
const pValue = (p) => (p && !p.overflow ? p.upper_ms : null);
const latency = (p) =>
  !p
    ? "—"
    : p.overflow
      ? ">120 s"
      : p.upper_ms >= 1000
        ? `≤${numeric(p.upper_ms / 1000, 2)} s`
        : `≤${number(p.upper_ms)} ms`;
const bytes = value => value === null || value === undefined ? "—"
  : value >= 2 ** 30 ? `${numeric(value / 2 ** 30, 2)} GiB`
  : value >= 2 ** 20 ? `${numeric(value / 2 ** 20, 1)} MiB`
  : value >= 1024 ? `${numeric(value / 1024, 1)} KiB` : `${number(value)} B`;
const percent = (part, whole) => whole ? `${numeric(part / whole * 100, 1)}%` : "—";
const badge = (text, kind = "") => el("span", text, `pill ${kind}`);
function options(id, rows, all) {
  const select = $(id),
    selected = select.value;
  select.replaceChildren(
    new Option(all, ""),
    ...rows.map((row) => new Option(row.title, row.id)),
  );
  select.value = rows.some((row) => row.id === selected) ? selected : "";
  if (selected && !rows.some(row => row.id === selected)) {
    select.add(new Option(`${selected} (not in inventory)`, selected));
    select.value = selected;
  }
}
function shape(svg, tag, attrs, text) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs))
    node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = text;
  svg.append(node);
  return node;
}
function svgNode(label, viewBox = "0 0 600 176") {
  const node = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  node.setAttribute("viewBox", viewBox);
  node.setAttribute("role", "img");
  node.setAttribute("aria-label", label);
  return node;
}
function spark(points, key, color) {
  const svg = svgNode("Recent trend", "0 0 100 32");
  svg.classList.add("mini-spark");
  const values = points.slice(-28).map(key),
    max = Math.max(1, ...values.filter((v) => v !== null));
  let path = "",
    active = false;
  values.forEach((value, i) => {
    if (value === null) {
      active = false;
      return;
    }
    path += `${active ? "L" : "M"}${(i / (values.length - 1 || 1)) * 100},${29 - (value / max) * 24} `;
    active = true;
  });
  shape(svg, "path", {
    d: path,
    fill: "none",
    stroke: color,
    "stroke-width": 1.6,
  });
  return svg;
}
function kpi(label, value, unit, detail, key, color, icon) {
  const node = el("div");
  append(
    node,
    append(
      el("div", undefined, "stat-heading"),
      el("span", label),
      el("span", icon, "stat-icon"),
    ),
  );
  const strong = append(el("strong"),el("span",value,"kpi-value"));
  if (unit) strong.append(el("small", unit));
  node.append(strong, el("p", detail), spark(state.data.charts, key, color));
  return node;
}
function boundScale(value) {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  const scaled = value / power;
  return (scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10) * power;
}
function lineChart(id, title, series, format) {
  const host = $(id);
  const restoreFocus = document.activeElement === host.querySelector("svg");
  const previousAt = Number(host.dataset.inspectedAt);
  const hadInspection = Number.isFinite(previousAt);
  host.replaceChildren();
  const width = Math.max(300, Math.min(800, host.clientWidth - 12));
  const svg = svgNode(title, `0 0 ${width} 176`);
  svg.setAttribute("tabindex", "0");
  svg.setAttribute("aria-describedby", `${id}-readout ${id}-instructions`);
  svg.setAttribute("aria-keyshortcuts", "ArrowLeft ArrowRight Home End Escape");
  const hidden = state.hiddenSeries.get(id) || new Set();
  const shown = series.filter((line) => !hidden.has(line.name));
  const points = state.data.charts,
    start = state.data.range_start_ms ?? state.data.now_ms - state.data.window_seconds * 1000,
    end = state.data.range_end_ms ?? state.data.now_ms;
  const valueOf = (line, point) => {
    const value = point.partial && !line.observed ? null : line.get(point);
    return Number.isFinite(value) ? value : null;
  };
  const all = shown
    .flatMap((line) =>
      (line.points || points).map((point) =>
        valueOf(line, point),
      ),
    )
    .filter((value) => value !== null && Number.isFinite(value));
  const upper = boundScale(all.reduce((max, value) => Math.max(max, value), 0) * 1.12),
    left = 42,
    right = width - 12,
    top = 14,
    bottom = 144;
  const x = (at) => left + ((at - start) / (end - start)) * (right - left),
    y = (value) => bottom - (value / upper) * (bottom - top);
  for (let i = 0; i < 4; i++) {
    const value = (upper * i) / 3,
      pos = y(value);
    shape(svg, "path", {
      d: `M${left} ${pos}H${right}`,
      stroke: "#e9eef2",
      "stroke-dasharray": "3 4",
    });
    shape(
      svg,
      "text",
      {
        x: left - 9,
        y: pos + 3,
        "text-anchor": "end",
        fill: "#a0adb6",
        "font-size": 10,
      },
      format(value, true),
    );
  }
  const tickCount = width < 450 ? 3 : 5;
  for (let i = 0; i < tickCount; i++)
    shape(
      svg,
      "text",
      {
        x: left + (i * (right - left)) / (tickCount - 1),
        y: 165,
        "text-anchor": i === 0 ? "start" : i === tickCount - 1 ? "end" : "middle",
        fill: "#a0adb6",
        "font-size": 10,
      },
      axisClock(start + ((end - start) * i) / (tickCount - 1)),
    );
  const gapMs = Math.max(state.data.bucket_seconds, state.data.sample_seconds) * 1800;
  shown.forEach((line, index) => {
    let d = "",
      active = false,
      previous = null,
      isolated = null;
    const markIsolated = () => {
      if (isolated)
        shape(svg, "circle", {
          cx: x(isolated.at_ms),
          cy: y(valueOf(line, isolated)),
          r: 2,
          fill: line.color,
        });
      isolated = null;
    };
    for (const point of line.points || points) {
      const value = valueOf(line, point);
      if (value === null || !Number.isFinite(value)) {
        markIsolated();
        active = false;
        previous = null;
        continue;
      }
      if (previous !== null && point.at_ms - previous > gapMs) {
        markIsolated();
        active = false;
      }
      isolated = active ? null : point;
      d += `${active ? "L" : "M"}${x(point.at_ms).toFixed(2)},${y(value).toFixed(2)} `;
      active = true;
      previous = point.at_ms;
    }
    markIsolated();
    shape(svg, "path", {
      d,
      fill: "none",
      stroke: line.color,
      "stroke-width": index === 0 ? 1.8 : 1.4,
      "stroke-linejoin": "round",
      "stroke-linecap": "round",
    });
  });
  if (!all.length)
    shape(
      svg,
      "text",
      {
        x: (left + right) / 2,
        y: 80,
        "text-anchor": "middle",
        fill: "#9aa8b1",
        "font-size": 11,
      },
      points.length
        ? "Metric not observed in this scope"
        : "No observations in this window",
    );
  const crosshair = shape(svg, "path", {
    d: "",
    stroke: "#acbac3",
    "stroke-dasharray": "3 3",
  });
  const tooltip = el("div", undefined, "tooltip");
  tooltip.hidden = true;
  tooltip.setAttribute("aria-hidden", "true");
  const readout = el("p", undefined, "chart-readout");
  readout.id = `${id}-readout`;
  readout.setAttribute("role", "status");
  readout.setAttribute("aria-live", "polite");
  readout.setAttribute("aria-atomic", "true");
  const instructions = el("p", "Hover or tap to inspect. Keyboard: left/right, Home/End. Missing measurements remain gaps; values are recorded observations, not interpolation.", "chart-instructions");
  instructions.id = `${id}-instructions`;
  const markers = shown.map((line) =>
    shape(svg, "circle", {
      r: 3,
      fill: line.color,
      stroke: "#fff",
      "stroke-width": 1,
      visibility: "hidden",
    }),
  );
  let focused = hadInspection ? BoboWatchMetrics.nearestIndex(points, previousAt)
    : Math.max(0, points.length - 1);
  let inspected = -1;
  function inspect(index, {reveal = true, persist = true} = {}) {
    if (!points.length) {
      readout.textContent = "No observations in this window. Missing data is unknown.";
      return;
    }
    focused = Math.max(0, Math.min(points.length - 1, index));
    const point = points[focused], at = point.at_ms;
    if (persist) host.dataset.inspectedAt = String(at);
    const stamp = rangeStamp(at) +
      (state.data.bucket_seconds < 1 ? `.${String(at % 1000).padStart(3, "0")}` : "");
    const heading = stamp + (point.partial ? " · partial observations" : "");
    // Pointer motion over the same observation does not repeatedly announce it.
    if (inspected !== focused) {
      const values = [];
      tooltip.replaceChildren(el("strong", heading));
      shown.forEach((line, i) => {
        const source = line.points || points,
          candidate = source[BoboWatchMetrics.nearestIndex(source, at)],
          sample = candidate?.at_ms === at ? candidate : null,
          value = sample ? valueOf(line, sample) : null;
        const missing = sample?.partial && !line.observed ? "Unavailable (partial telemetry)"
          : !sample ? "No matching observation" : "No recorded value";
        const text = value === null ? missing : line.format ? line.format(value) : format(value, false);
        values.push(`${line.name}: ${text}`);
        tooltip.append(append(el("div"), el("span", line.name), el("b", text)));
        markers[i].dataset.available = String(value !== null);
        if (value !== null) {
          markers[i].setAttribute("cx", x(at));
          markers[i].setAttribute("cy", y(value));
        }
      });
      readout.textContent = `${heading} · ${values.join(" · ")}`;
      readout.dataset.atMs = String(at);
      inspected = focused;
    }
    if (reveal) {
      crosshair.setAttribute("d", `M${x(at)} ${top}V${bottom}`);
      markers.forEach(marker => marker.setAttribute("visibility", marker.dataset.available === "true" ? "visible" : "hidden"));
      tooltip.hidden = false;
    }
  }
  function dismiss() {
    tooltip.hidden = true;
    crosshair.setAttribute("d", "");
    markers.forEach(marker => marker.setAttribute("visibility", "hidden"));
    // The textual readout stays available after touch, blur and pointer leave.
  }
  function inspectPointer(event) {
    const transform = svg.getScreenCTM();
    if (!transform) return;
    const cursor = svg.createSVGPoint();
    cursor.x = event.clientX;
    cursor.y = event.clientY;
    const local = cursor.matrixTransform(transform.inverse());
    const at = start + Math.max(0, Math.min(1, (local.x - left) / (right - left))) * (end - start);
    inspect(BoboWatchMetrics.nearestIndex(points, at));
  }
  svg.addEventListener("pointerdown", inspectPointer);
  svg.addEventListener("pointermove", inspectPointer);
  svg.addEventListener("pointerleave", () => {
    if (document.activeElement !== svg) dismiss();
  });
  svg.addEventListener("keydown", (event) => {
    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      inspect(event.key === "Home" ? 0 : event.key === "End" ? points.length - 1
        : focused + (event.key === "ArrowLeft" ? -1 : 1));
    } else if (event.key === "Escape") {
      event.preventDefault();
      dismiss();
    }
  });
  svg.addEventListener("focus", () => inspect(focused));
  svg.addEventListener("blur", dismiss);
  host.append(svg, readout, instructions, tooltip);
  inspect(focused, {reveal: false, persist: false});
  if (restoreFocus) svg.focus({preventScroll: true});
  const legend = $(id.replace("-chart", "-legend"));
  if (legend)
    legend.replaceChildren(
      ...series.map((line) => {
        const key = el("i", "", "key");
        key.style.backgroundColor = line.color;
        const button = append(
          el("button", undefined, "legend-toggle"),
          key,
          el("span", line.caption ? `${line.name} ${line.caption}` : line.name),
        );
        button.type = "button";
        button.dataset.series = line.name;
        button.setAttribute("aria-pressed", String(!hidden.has(line.name)));
        button.disabled = shown.length === 1 && !hidden.has(line.name);
        button.addEventListener("click", () => {
          if (hidden.has(line.name)) hidden.delete(line.name);
          else hidden.add(line.name);
          state.hiddenSeries.set(id, hidden);
          render();
          const replacement = Array.from(
            $(id.replace("-chart", "-legend")).querySelectorAll("button"),
          ).find((node) => node.dataset.series === line.name);
          replacement?.focus({preventScroll: true});
        });
        return button;
      }),
    );
}
function serviceComparison() {
  const host = $("service-comparison");
  const sites = BoboWatchMetrics.compareSites(state.data.sites, $("service-sort").value);
  if (!sites.length) {
    host.replaceChildren(el("p", "No sites in this scope. Adjust the filters to compare sites.", "empty"));
    return;
  }
  const rows = sites.map(site => {
    const metrics = site.http, q = metrics.quality;
    const active = site.replicas.filter(r => !r.retired);
    const fresh = active.filter(r => r.status === "fresh").length;
    const status = state.error ? "Updates unavailable" : state.data.historical
      ? metrics.snapshot_count ? "Recorded" : "No observations"
      : !active.length ? "Retired site" : `${fresh}/${active.length} reporting`;
    const focus = el("button", `${site.title} ↗`, "compare-site");
    focus.dataset.site = site.id;
    focus.setAttribute("aria-label", `Focus site ${site.title}`);
    const identity = append(el("td"), focus, el("small", `${site.project_title} · ${site.environment}`));
    const cell = (label, value, detail, cls = "") => {
      const td = append(el("td", undefined, `numeric ${cls}`), el("strong", value));
      td.dataset.label = label;
      if (detail) td.append(el("small", detail));
      return td;
    };
    const traffic = cell("Requests", number(metrics.requests), `${percent(metrics.requests, state.data.http.requests)} of selected traffic`);
    const track = el("span", undefined, "comparison-track"), fill = el("i");
    fill.style.width = `${state.data.http.requests ? metrics.requests / state.data.http.requests * 100 : 0}%`;
    track.append(fill); traffic.append(track);
    const telemetry = append(el("td", undefined, "compare-telemetry"), badge(status,
      state.error || !state.data.historical && fresh < active.length ? "warning" : "neutral"));
    telemetry.firstChild.dataset.liveSite = site.id;
    telemetry.dataset.label = "Telemetry";
    telemetry.append(el("small", `${number(metrics.snapshot_count)} samples${q.partial ? ` · ${number(q.partial)} partial` : ""}${q.skewed ? ` · ${number(q.skewed)} skewed` : ""}`));
    return append(el("tr", undefined, "service-row"), identity, traffic,
      cell("5xx responses", number(metrics.errors), `${percent(metrics.errors, metrics.requests)} of requests`, metrics.errors ? "error-number" : ""),
      cell("Mean response", metrics.mean_ms === null ? "—" : `${numeric(metrics.mean_ms)} ms`, "Weighted mean"),
      cell("p95 bound", latency(metrics.latency.p95), "Histogram bound"),
      cell("Responses >1s", number(metrics.slow_requests), `${percent(metrics.slow_requests, metrics.requests)} of requests`), telemetry);
  });
  const comparison = table(["Site / project", "Requests", "5xx responses", "Mean response", "p95 bound", ">1s", "Telemetry"], rows);
  comparison.setAttribute("role", "table");
  for (const group of comparison.querySelectorAll("thead,tbody")) group.setAttribute("role", "rowgroup");
  for (const row of comparison.querySelectorAll("tr")) row.setAttribute("role", "row");
  for (const cell of comparison.querySelectorAll("th,td")) cell.setAttribute("role", cell.tagName === "TH" ? "columnheader" : "cell");
  host.replaceChildren(comparison);
}
const durationLimits = [5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 120000];
const durationLabel = value => value >= 1000 ? `${numeric(value / 1000, 1)} s` : `${number(value)} ms`;
function durationInterval(index) {
  if (!index) return `0–${durationLabel(durationLimits[0])}`;
  if (index === durationLimits.length) return `>${durationLabel(durationLimits.at(-1))}`;
  return `>${durationLabel(durationLimits[index - 1])} to ≤${durationLabel(durationLimits[index])}`;
}
function latencyHeatmap() {
  const data = state.data, metrics = data.http, host = $("duration-heatmap");
  const focused = host.contains(document.activeElement);
  const width = Math.max(300, Math.min(1200, host.clientWidth - 2));
  const left = 62, right = width - 12, top = 14, rowHeight = 13, bottom = top + 14 * rowHeight;
  const columns = BoboWatchMetrics.latencyHeatmap(data.charts, {
    start: data.range_start_ms, end: data.range_end_ms,
    columns: Math.max(1, Math.floor((right - left) / 6)),
    bucket_ms: Math.max(1, Math.round(data.bucket_seconds * 1000)),
  });
  const share = $("heatmap-mode").value === "share";
  const total = metrics.buckets.reduce((sum, value) => sum + value, 0);
  const counts = [metrics.buckets.slice(0, 5), metrics.buckets.slice(5, 8), metrics.buckets.slice(8)]
    .map(bins => bins.reduce((sum, value) => sum + value, 0));
  $("latency-bands").replaceChildren(...counts.map((count, index) => {
    const band = el("div", undefined, `latency-band band-${index}`);
    append(band, el("span", ["≤100 ms", ">100 ms to ≤1 s", ">1 s"][index]),
      el("strong", percent(count, total)), el("small", `${number(count)} requests`));
    const track = el("span", undefined, "band-track"), fill = el("i");
    fill.style.width = `${total ? count / total * 100 : 0}%`; track.append(fill); band.append(track);
    return band;
  }));
  const q = metrics.quality;
  $("quality-summary").replaceChildren(...[
    [number(q.snapshots), "source observations", "Each retained snapshot is counted. Many source observations can contribute to one chart group."],
    [number(q.partial), "partial observations", "Source observations flagged partial, including baselines or long reporting intervals. This can overlap clock skew."],
    [number(q.skewed), "clock-skewed observations", "Source observations whose timestamps differ too far from receipt time. This can overlap partial observations."],
    [percent(q.extended_http, q.snapshots), "extended HTTP samples", `${number(q.extended_http)} of ${number(q.snapshots)} snapshots include status, endpoint and body-size fields. Request-weighted coverage appears above.`],
  ].map(([value, label, title]) => {
    const item = append(el("div"), el("strong", value), el("span", label)); item.title = title; return item;
  }));
  const svg = svgNode("Request duration over time. Left and right arrows move through time; up and down arrows move through duration bins.", `0 0 ${width} ${bottom + 47}`);
  svg.setAttribute("tabindex", "0");
  svg.setAttribute("aria-describedby", "heatmap-readout heatmap-note");
  const defs = shape(svg, "defs", {});
  const pattern = shape(defs, "pattern", {id: "heatmap-missing", width: 6, height: 6, patternUnits: "userSpaceOnUse"});
  shape(pattern, "rect", {width: 6, height: 6, fill: "#f0f3f5"});
  shape(pattern, "path", {d: "M-1 1L1 -1M0 6L6 0M5 7L7 5", stroke: "#dce4e9", "stroke-width": 1});
  shape(svg, "rect", {x: left, y: top, width: right-left, height: bottom-top, fill: "url(#heatmap-missing)"});
  let maximum = 0;
  for (const column of columns) for (const value of column.buckets) maximum = Math.max(maximum, value);
  const palette = [[222, 242, 238], [129, 202, 196], [48, 153, 165], [30, 102, 128], [24, 58, 86]];
  const color = fraction => {
    const at = Math.min(palette.length - 1, Math.max(0, fraction) * (palette.length - 1));
    const index = Math.min(palette.length - 2, Math.floor(at)), part = at - index;
    return `rgb(${palette[index].map((value, channel) => Math.round(value + (palette[index + 1][channel] - value) * part)).join(",")})`;
  };
  const xAt = at => left + (at - data.range_start_ms) / (data.range_end_ms - data.range_start_ms) * (right - left);
  columns.forEach((column, index) => {
    if (!column.observed) return;
    const x = xAt(column.start_ms), cellWidth = xAt(column.end_ms) - x;
    shape(svg, "rect", {x, y: top, width: cellWidth, height: bottom-top, fill: "#f8fbfc"});
    column.buckets.forEach((count, bin) => {
      if (!count) return;
      const intensity = share ? count / column.requests : Math.log1p(count) / Math.log1p(maximum);
      shape(svg, "rect", {x, y: top + (13-bin)*rowHeight, width: Math.max(.1, cellWidth - .35), height: rowHeight - .5,
        fill: color(intensity)});
    });
    shape(svg, "rect", {x, y: bottom+5, width: Math.max(.1, cellWidth - .35), height: 4,
      fill: column.quality.skewed ? "#b67c25" : column.partial ? "#d6b26b" : "#7cb9ae"});
  });
  for (const bin of [0, 2, 4, 6, 7, 9, 11, 13]) {
    const label = bin === 13 ? ">120 s" : `≤${durationLabel(durationLimits[bin])}`;
    shape(svg, "text", {x: left-9, y: top+(13-bin)*rowHeight+9, "text-anchor": "end", fill: "#627680", "font-size": 10}, label);
  }
  const tickCount = width < 600 ? 3 : 5;
  for (let i = 0; i < tickCount; i++) shape(svg, "text", {
    x: left+(right-left)*i/(tickCount-1), y: bottom+29, "text-anchor": i===0?"start":i===tickCount-1?"end":"middle", fill: "#627680", "font-size": 10,
  }, axisClock(data.range_start_ms+(data.range_end_ms-data.range_start_ms)*i/(tickCount-1)));
  if (!q.snapshots) shape(svg, "text", {x:(left+right)/2,y:top+(bottom-top)/2,"text-anchor":"middle",fill:"#526875","font-size":12}, "No retained observations in this range");
  const marker = shape(svg, "rect", {x:left,y:top,width:1,height:rowHeight,fill:"none",stroke:"#203b4b","stroke-width":1.5,"pointer-events":"none",visibility:"hidden"});
  let selected = Math.max(0, columns.findLastIndex(column => column.observed)), bin = 7;
  const inspect = () => {
    const column = columns[selected];
    marker.setAttribute("x", xAt(column.start_ms)); marker.setAttribute("width", xAt(column.end_ms)-xAt(column.start_ms)); marker.setAttribute("y", top+(13-bin)*rowHeight); marker.setAttribute("visibility", "visible");
    const interval = `${rangeStamp(column.start_ms)} → ${rangeStamp(column.end_ms)}`;
    $("heatmap-readout").textContent = !column.observed
      ? `${interval} · No metric snapshots. Request counts are unknown.`
      : `${interval} · ${durationInterval(bin)}: ${number(column.buckets[bin])} requests · ${percent(column.buckets[bin], column.requests)} of ${number(column.requests)} completed requests · ${number(column.quality.snapshots)} observations${column.partial ? " · Partial fleet/interval coverage" : ""}${column.quality.skewed ? ` · ${number(column.quality.skewed)} clock-skewed` : ""}`;
  };
  svg.addEventListener("focus", inspect);
  svg.addEventListener("keydown", event => {
    if (!["ArrowLeft","ArrowRight","ArrowUp","ArrowDown","Home","End"].includes(event.key)) return;
    event.preventDefault();
    if (event.key === "ArrowLeft") selected = Math.max(0, selected-1);
    if (event.key === "ArrowRight") selected = Math.min(columns.length-1, selected+1);
    if (event.key === "ArrowUp") bin = Math.min(13, bin+1);
    if (event.key === "ArrowDown") bin = Math.max(0, bin-1);
    if (event.key === "Home") selected = 0;
    if (event.key === "End") selected = columns.length-1;
    inspect();
  });
  const point = event => {
    const pointer = svg.createSVGPoint(); pointer.x = event.clientX; pointer.y = event.clientY;
    const matrix = svg.getScreenCTM(); if (!matrix) return;
    const {x, y} = pointer.matrixTransform(matrix.inverse());
    if (x < left || x > right || y < top || y > bottom) return;
    const at = data.range_start_ms + (x-left)/(right-left)*(data.range_end_ms-data.range_start_ms);
    let first = 0, last = columns.length-1;
    while (first < last) {
      const middle = Math.floor((first+last)/2);
      if (at >= columns[middle].end_ms) first = middle+1; else last = middle;
    }
    selected = first;
    bin = Math.min(13, Math.max(0, 13-Math.floor((y-top)/rowHeight))); inspect();
  };
  svg.addEventListener("pointermove", event => { if (event.pointerType !== "touch") point(event); });
  svg.addEventListener("pointerdown", point);
  host.replaceChildren(svg);
  $("heatmap-readout").textContent = "Hover or tap to inspect exact counts. Keyboard: ← → time, ↑ ↓ duration bins.";
  $("heatmap-note").textContent = `${number(total)} measured requests · ${number(data.charts.length)} chart timestamps summed into ${number(columns.length)} display columns. Every histogram count is included.`;
  $("heatmap-key-min").textContent = share ? "0%" : maximum ? "1" : "0";
  $("heatmap-key-max").textContent = share ? "100% of column" : `${number(maximum)} requests · log color`;
  if (focused) svg.focus({preventScroll: true});
}

function histogram() {
  const host = $("histogram-chart");
  host.replaceChildren();
  const buckets = state.data.http.buckets,
    total = buckets.reduce((a, b) => a + b, 0),
    plotWidth = Math.max(300, Math.min(800, host.clientWidth - 12)),
    svg = svgNode("Request duration histogram", `0 0 ${plotWidth} 176`);
  const max = Math.max(1, ...buckets),
    left = 42,
    right = plotWidth - 12,
    top = 14,
    bottom = 144,
    width = (right - left) / buckets.length;
  for (let i = 0; i < 4; i++) {
    const value = (max * i) / 3,
      pos = bottom - (value / max) * (bottom - top);
    shape(svg, "path", {
      d: `M${left} ${pos}H${right}`,
      stroke: "#e9eef2",
      "stroke-dasharray": "3 4",
    });
    shape(
      svg,
      "text",
      {
        x: left - 8,
        y: pos + 3,
        "text-anchor": "end",
        fill: "#a0adb6",
        "font-size": 10,
      },
      value >= 1000 ? `${numeric(value / 1000)}k` : number(value),
    );
  }
  const labels = [
    "5ms",
    "10ms",
    "25ms",
    "50ms",
    "100ms",
    "250ms",
    "500ms",
    "1s",
    "2.5s",
    "5s",
    "10s",
    "30s",
    "120s",
    ">120s",
  ];
  buckets.forEach((value, index) => {
    const height = (value / max) * (bottom - top),
      bar = shape(svg, "rect", {
        x: left + index * width + 5,
        y: bottom - height,
        width: width - 10,
        height,
        rx: 2,
        fill: index >= 9 ? colors.amber : colors.teal,
        opacity: index >= 9 ? 0.8 : 0.7,
      });
    shape(
      bar,
      "title",
      {},
      `${labels[index]} upper bound: ${number(value)} requests (${total ? numeric((value / total) * 100) : 0}%)`,
    );
    if ((index % 2 === 0 && index < 12) || index === 13)
      shape(
        svg,
        "text",
        {
          x: left + (index + 0.5) * width,
          y: 164,
          "text-anchor": "middle",
          fill: "#a0adb6",
          "font-size": 9,
        },
        labels[index],
      );
  });
  if (!total)
    shape(
      svg,
      "text",
      {
        x: (left + right) / 2,
        y: 78,
        "text-anchor": "middle",
        fill: "#9aa8b1",
        "font-size": 11,
      },
      "No duration observations",
    );
  host.append(svg);
  const slow = buckets.slice(8).reduce((sum, value) => sum + value, 0);
  $("histogram-count").textContent =
    `${number(total)} requests · ${total ? numeric((slow / total) * 100) : "0"}% >1s`;
}
function responseMix() {
  const data = state.data.http,
    host = $("status-mix");
  host.replaceChildren();
  if (!data.status_classes) {
    host.append(
      el(
        "p",
        "Status classes were not supplied by the selected producers.",
        "empty",
      ),
    );
    $("status-coverage").textContent = "Not observed";
    return;
  }
  const total = data.http_requests,
    colorsByClass = ["purple", "teal", "blue", "amber", "red"];
  const track = el("div", undefined, "response-track");
  data.status_classes.forEach((value, i) => {
    const segment = el("span", undefined, colorsByClass[i]);
    segment.style.width = `${total ? (value / total) * 100 : 0}%`;
    segment.title = `${i + 1}xx: ${number(value)}`;
    track.append(segment);
  });
  host.append(track);
  const names = [
    "1xx Informational",
    "2xx Successful",
    "3xx Redirection",
    "4xx Client errors",
    "5xx Server errors",
  ];
  data.status_classes.forEach((value, i) =>
    host.append(
      append(
        el("div", undefined, "response-row"),
        append(
          el("span"),
          el("i", "", `key ${colorsByClass[i]}`),
          el("span", names[i]),
        ),
        el("strong", number(value)),
        el("span", total ? `${numeric((value / total) * 100)}%` : "—"),
      ),
    ),
  );
  $("status-coverage").textContent =
    `${data.requests ? number((total / data.requests) * 100) : 0}% coverage`;
}
function table(headers, rows) {
  const node = el("table"),
    head = el("tr");
  headers.forEach((name, i) => {
    const cell = el("th", name, i ? "numeric" : "");
    cell.setAttribute("scope", "col");
    head.append(cell);
  });
  node.append(append(el("thead"), head), replaceAllChildren(el("tbody"), rows));
  return node;
}
function routeTable() {
  const host = $("route-table");
  host.replaceChildren();
  const routes = state.data.http.routes;
  const selection = BoboWatchMetrics.endpoints(routes, {
    query: $("route-search").value, sort: $("route-sort").value,
    minimum: Number($("route-minimum").value), page: state.routePage,
    size: $("route-page-size").value,
  });
  state.routePage = selection.page;
  $("route-coverage").textContent = `${number(routes.length)} endpoint templates · all matching templates loaded · ${percent(state.data.http.http_requests, state.data.http.requests)} request coverage. Shares use all instrumented endpoints before filtering; response time includes overlapping requests and is not CPU time.`;
  $("route-count").textContent = selection.total
    ? `${selection.first}–${selection.last} of ${selection.total} matches`
    : "0 matches";
  $("route-previous").disabled = selection.page === 0;
  $("route-next").disabled = selection.page >= selection.pages - 1;
  if (!selection.rows.length) {
    host.append(
      el(
        "p",
        routes.length ? "No matching endpoints. Try a different search or minimum request count."
          : "No endpoint observations in this window. Producers must supply registered route templates.",
        "empty",
      ),
    );
    return;
  }
  const totals = BoboWatchMetrics.endpointContributions(routes);
  const rank = $("route-sort").value;
  const score = route => rank === "errors" ? route.errors
    : rank === "error-rate" ? route.requests ? route.errors / route.requests : 0
    : rank === "mean" ? route.requests ? route.duration_us / route.requests : 0
    : rank === "work" ? route.duration_us : route.requests;
  const maximum = routes.reduce((max, row) => Math.max(max, score(row)), 0);
  const contribution = (value, denominator, noun, cls = "numeric") => {
    const td = el("td", undefined, cls);
    td.append(el("strong", value), el("small", `${percent(denominator[0], denominator[1])} of endpoint ${noun}`));
    td.title = "Share of all loaded instrumented endpoints in the selected scope and time range; search and pagination do not change the denominator.";
    return td;
  };
  host.append(
    table(
      ["Endpoint / site", "Requests", "5xx", "5xx rate", "Mean time", "Total time"],
      selection.rows
        .map((route) => {
          const site = el("button", `${route.site} ↗`, "endpoint-site");
          site.dataset.site = route.site;
          site.setAttribute("aria-label", `Focus site ${route.site}`);
          const first = append(
            el("td"),
            el("span", route.route, "endpoint"),
            site,
          );
          const bar = el("div", undefined, "route-bar"),
            fill = el("i");
          fill.style.width = `${maximum ? score(route) / maximum * 100 : 0}%`;
          fill.className = ["errors", "error-rate"].includes(rank) ? "error-fill" : ["mean", "work"].includes(rank) ? "duration-fill" : "";
          bar.title = `Relative ${$("route-sort").selectedOptions[0].textContent.toLowerCase()} across all loaded endpoints`;
          bar.append(fill);
          first.append(bar);
          return append(
            el("tr"),
            first,
            contribution(number(route.requests), [route.requests, totals.requests], "traffic"),
            contribution(number(route.errors), [route.errors, totals.errors], "5xx", route.errors ? "numeric error-number" : "numeric"),
            el(
              "td",
              percent(route.errors, route.requests),
              "numeric",
            ),
            el(
              "td",
              route.requests ? `${numeric(route.duration_us / route.requests / 1000)} ms` : "—",
              "numeric",
            ),
            contribution(`${numeric(route.duration_us / 1e6, 2)} s`, [route.duration_us, totals.duration_us], "response time"),
          );
        }),
    ),
  );
}
function renderEvents() {
  if (!state.data) return;
  const selected = BoboWatchMetrics.events(state.data.events, {
    query: $("event-search").value, severity: $("event-severity").value,
    page: state.eventPage, size: $("event-page-size").value,
  });
  state.eventPage = selected.page;
  $("event-coverage").textContent = `${number(state.data.events.length)} events loaded · full range`;
  $("event-count").textContent = selected.total ? `${selected.first}–${selected.last} of ${number(selected.total)} matches` : "0 matches";
  $("event-previous").disabled = selected.page === 0;
  $("event-next").disabled = selected.page >= selected.pages - 1;
  replaceAllChildren($("events"), selected.rows.length ? selected.rows.map(event => {
    const stamp = el("time", rangeStamp(event.occurred_at_ms));
    stamp.dateTime = new Date(event.occurred_at_ms).toISOString();
    const inspect = el("button","Inspect time ±5m","event-inspect");
    inspect.type = "button";
    inspect.dataset.eventAt = event.occurred_at_ms;
    return append(el("div", undefined, "event"), stamp,
      el("span", event.severity, `severity ${event.severity}`),
      el("div", event.message, "message"),
      append(el("span", undefined, "scope"), el("span", `${event.site} / ${event.replica}`), inspect));
  }) : [el("p", state.data.events.length ? "No events match these filters." : "No operational events in this window.", "empty")]);
}
function siteCard(site) {
  const historical = state.data.historical;
  const active = site.replicas.filter(r => !r.retired);
  const node = el("article", undefined, "site"),
    fresh = state.error
      ? 0
      : site.replicas.filter((r) => r.status === "fresh").length;
  const title = append(
    el("div"),
    el("span", `${site.project_title} / ${site.environment}`, "eyebrow"),
    el("h2", site.title),
    el("p", site.id),
  );
  const focus = el("button", "Focus site ↗", "focus-site");
  focus.dataset.site = site.id;
  title.append(focus);
  node.append(
    append(
      el("div", undefined, "site-top"),
      title,
      badge(
        state.error
          ? "Updates unavailable"
          : historical ? "Selected range"
          : !active.length ? "Retired site"
          : fresh === active.length
            ? "Telemetry current"
            : `${fresh}/${active.length} reporting`,
        state.error || (!historical && fresh < active.length) ? "warning" : !active.length ? "neutral" : "",
      ),
    ),
  );
  node.querySelector(".site-top .pill").dataset.liveSite = site.id;
  const values = [
    [
      historical ? "Requests observed" : "Current traffic",
      historical ? number(site.requests)
        : site.current.rps === null || state.error ? "—" : numeric(site.current.rps),
      historical ? "requests" : "req/s",
    ],
    ["p95 bound", latency(site.http.latency.p95), ""],
    [
      "Server errors",
      site.requests ? `${numeric((site.errors / site.requests) * 100)}%` : "—",
      "5xx",
    ],
  ];
  node.append(
    append(
      el("div", undefined, "site-stats"),
      ...values.map(([label, value, unit]) =>
        append(
          el("div"),
          el("small", label),
          append(el("strong"), el("span",value), el("em", unit)),
        ),
      ),
    ),
  );
  if (!historical) node.querySelector(".site-stats strong span").dataset.currentSite = site.id;
  const rows = site.replicas.map((replica) => {
    const status = state.error
      ? "Updates unavailable"
      : historical ? (replica.http.snapshot_count ? "Recorded" : "No data")
      : {
          fresh: "Reporting",
          stale: "Stale",
          waiting: "Waiting",
          skewed: "Clock skew",
          retired: "Retired",
        }[replica.status];
    const button = el("button", `${replica.id}${replica.retired ? " (retired)" : ""} ↗`, "replica-button");
    button.dataset.replica = replica.id;
    const identity = append(
      el("td"),
      button,
      el("small", `${replica.host_id} · ${historical
        ? replica.range_observed_at_ms ? rangeStamp(replica.range_observed_at_ms) : "No observations"
        : age(replica.observed_at_ms)}`),
    );
    const row = append(
      el("tr"),
      identity,
      el("td", historical ? number(replica.http.requests) : state.error ? "—" : numeric(replica.current.rps), "numeric"),
      el("td", latency(replica.http.latency.p95), "numeric"),
      el(
        "td",
        replica.http.requests
          ? `${numeric((replica.http.errors / replica.http.requests) * 100)}%`
          : "—",
        "numeric",
      ),
      el(
        "td",
        historical ? number(replica.http.snapshot_count) : state.error ? "—" : number(replica.current.in_flight),
        "numeric",
      ),
      append(
        el("td"),
        badge(
          status,
          state.error || (!historical && !replica.retired && replica.status !== "fresh") ? "warning" : replica.retired ? "neutral" : "",
        ),
      ),
    );
    row.querySelector(".pill").dataset.liveReplica = replica.id;
    if (!historical) {
      row.children[1].dataset.currentReplica = replica.id;
      row.children[4].dataset.inflightReplica = replica.id;
      row.querySelector("td small").dataset.replicaAge = replica.id;
    }
    return row;
  });
  node.append(
    append(
      el("div", undefined, "table-scroll"),
      table(["Replica", historical ? "Requests" : "req/s", "p95", "5xx", historical ? "Samples" : "Active", historical ? "History" : "Telemetry"], rows),
    ),
    el(
      "p",
      `${number(site.requests)} requests observed · ${site.partial ? "Partial intervals included" : "Selected time window"}`,
      "site-coverage",
    ),
  );
  return node;
}
const viewControls = {
  window: "window", detail: "chart-detail", refresh: "auto-refresh", traffic: "traffic-view",
  errors: "error-view", heatmap: "heatmap-mode", serviceSort: "service-sort",
  routeQuery: "route-search", routeSort: "route-sort", routeMinimum: "route-minimum", routeSize: "route-page-size",
  eventQuery: "event-search", eventSeverity: "event-severity", eventSize: "event-page-size",
};
function viewState() {
  return {...Object.fromEntries(Object.entries(viewControls).map(([key,id]) => [key,$(id).value])),
    ...Object.fromEntries(["project","site","host"].map(id => [id,$(id).value])),
    replica:state.replica, custom:state.customRange};
}
function viewMessage(message) {
  $("workspace-message").textContent = message;
  $("workspace-message").hidden = !message;
}
function applyLocation() {
  const decoded = BoboWatchWorkspace.decode(location.search);
  state.workspaceInvalid = !decoded.valid;
  $("reset-view").hidden = decoded.valid;
  if (!decoded.valid) {
    viewMessage(`${decoded.warnings.join(" ")} No query has been made for this invalid link.`);
    return false;
  }
  if (!decoded.present) return true;
  const view = decoded.state;
  for (const [key,id] of Object.entries(viewControls)) if (key in view) $(id).value = view[key];
  for (const id of ["project","site","host"]) {
    if (view[id] && ![...$(id).options].some(option => option.value === view[id])) $(id).add(new Option(view[id],view[id]));
    $(id).value = view[id];
  }
  state.replica = view.replica;
  if (view.custom) state.customRange = view.custom;
  state.refreshSeconds = Number($("auto-refresh").value);
  state.routePage = state.eventPage = 0;
  viewMessage("");
  syncTimeControls();
  return true;
}
function recordLocation(replace = false) {
  if (state.workspaceInvalid) return;
  try {
    const query = BoboWatchWorkspace.encode(viewState());
    if (location.search.slice(1) !== query) {
      history[replace || state.initialLocation ? "replaceState" : "pushState"](null,"",`${location.pathname}?${query}${location.hash}`);
    }
    state.initialLocation = false;
  } catch (error) { viewMessage(error.message); }
}
function applyExactRange(range) {
  state.customRange = range;
  $("window").value = "custom";
  state.routePage = state.eventPage = 0;
  syncTimeControls();
  savePreferences();
  refresh();
}
function renderRangeNavigation() {
  const data = state.data;
  $("copy-view").disabled = !data;
  for (const [id,direction] of [["previous-range",-1],["next-range",1]]) {
    try {
      if (!data) throw new Error();
      BoboWatchWorkspace.shiftRange(data,direction);
      $(id).disabled = false;
    } catch { $(id).disabled = true; }
  }
  $("live-range").hidden = !data || !data.historical;
}
function telemetryNow() {
  return state.data ? state.data.now_ms + Math.max(0, performance.now() - state.loadedMono) : Date.now();
}
function renderFreshness() {
  if (!state.data || $("dashboard").hidden) return;
  const data = state.data, now = telemetryNow(), stale = data.stale_seconds;
  const replicas = data.sites.flatMap(site => site.replicas), active = replicas.filter(r => !r.retired);
  const statusOf = r => BoboWatchOperations.status(r, now, stale);
  const fresh = active.filter(r => statusOf(r) === "fresh").length;
  const interrupted = !!state.error || !navigator.onLine;
  const current = BoboWatchOperations.current(data.current, replicas, now, stale, interrupted);
  const snapshotAge = Math.max(0, Math.floor((performance.now() - state.loadedMono) / 1000));
  $("updated").textContent = `As of ${detailClock(data.now_ms)} · ${snapshotAge < 60 ? `${snapshotAge}s` : `${Math.floor(snapshotAge / 60)}m`} ago`;
  $("connection").textContent = !navigator.onLine ? "Browser offline" : interrupted ? "Updates interrupted" : "Last query succeeded";
  $("connection").className = `pill ${interrupted ? "warning" : "neutral"}`;
  $("refresh-status").textContent = state.busy ? "Previous view remains visible while querying"
    : document.hidden ? "Auto-refresh paused while hidden"
    : !state.refreshSeconds ? "Auto-refresh off · showing last queried view"
    : state.nextRefresh ? `Next query in ${Math.max(0, Math.ceil((state.nextRefresh - performance.now()) / 1000))}s` : "";
  if (!data.historical) {
    $("summary").children[0].querySelector(".kpi-value").textContent = numeric(current.rps);
    $("summary").children[3].querySelector(".kpi-value").textContent = number(current.in_flight);
    $("summary").children[3].querySelector("p").textContent = `${fresh} / ${active.length} active replicas reporting`;
    $("telemetry-status").textContent = interrupted ? "Updates unavailable" : `${fresh}/${active.length} active replicas reporting`;
    $("telemetry-status").className = `pill ${interrupted || fresh < active.length ? "warning" : "neutral"}`;
    $("replica-count").textContent = `${fresh} / ${active.length} active reporting · select a replica to inspect`;
    if ($("traffic-view").value !== "count") $("traffic-value").textContent = `${numeric(current.rps)} req/s`;
  }
  for (const node of document.querySelectorAll("[data-live-site]")) {
    const site = data.sites.find(s => s.id === node.dataset.liveSite);
    const expected = site.replicas.filter(r => !r.retired), reporting = expected.filter(r => statusOf(r) === "fresh").length;
    if (data.historical) continue;
    node.textContent = interrupted ? "Updates unavailable" : !expected.length ? "Retired site" : `${reporting}/${expected.length} reporting`;
    node.className = `pill ${interrupted || reporting < expected.length ? "warning" : "neutral"}`;
  }
  for (const node of document.querySelectorAll("[data-current-site]")) {
    const site = data.sites.find(s => s.id === node.dataset.currentSite);
    node.textContent = numeric(BoboWatchOperations.current(site.current, site.replicas, now, stale, interrupted).rps);
  }
  for (const node of document.querySelectorAll("[data-live-replica], [data-current-replica], [data-inflight-replica], [data-replica-age]")) {
    const id = node.dataset.liveReplica || node.dataset.currentReplica || node.dataset.inflightReplica || node.dataset.replicaAge;
    const replica = replicas.find(r => r.id === id);
    if (!replica) continue;
    const status = statusOf(replica);
    if (node.dataset.replicaAge) {
      const seconds = replica.observed_at_ms == null ? null : Math.max(0, Math.floor((now - replica.observed_at_ms) / 1000));
      node.textContent = `${replica.host_id} · ${seconds === null ? "Not observed" : `${seconds}s since observation`}`;
    } else if (!data.historical && node.dataset.liveReplica) {
      node.textContent = interrupted ? "Updates unavailable" : {fresh:"Reporting",stale:"Stale",waiting:"Waiting",skewed:"Clock skew",retired:"Retired"}[status];
      node.className = `pill ${interrupted || !["fresh","retired"].includes(status) ? "warning" : "neutral"}`;
    } else if (!data.historical) {
      const value = BoboWatchOperations.current(replica.current, [replica], now, stale, interrupted);
      node.textContent = node.dataset.currentReplica ? numeric(value.rps) : number(value.in_flight);
    }
  }
  for (const node of document.querySelectorAll("[data-telemetry-replica]")) {
    const replica = replicas.find(r => r.id === node.dataset.telemetryReplica);
    if (replica) node.textContent = interrupted ? "Updates unavailable" : statusOf(replica);
  }
  const coverage = !data.http.snapshot_count && data.historical ? "No retained metric snapshots in this range."
    : data.retained?.oldest_snapshot_at_ms > data.range_start_ms
      ? "This range starts before this scope’s oldest retained metric observation. Recorded extent does not establish continuous coverage." : "";
  const message = state.error || (!navigator.onLine ? "Browser offline. Displayed observations remain available; current gauges are unknown."
    : state.epochChanged ? "The RAM history epoch changed. Earlier monitoring history is no longer retained."
    : coverage || (!data.historical && fresh < active.length ? "Some expected replicas have no current telemetry. Current gauges expire; recorded totals remain available." : ""));
  $("notice").textContent = message;
  $("notice").hidden = !message;
}
function renderOperations() {
  const info = state.operations;
  $("refresh-operations").disabled = state.operationsBusy;
  if (!info) {
    $("operations-message").textContent = state.operationsError || "Waiting for runtime diagnostics.";
    return;
  }
  const storage = info.storage, capacity = BoboWatchOperations.capacity(storage);
  $("operations-message").textContent = state.operationsError
    ? `${state.operationsError} Showing the previous check from ${rangeStamp(info.now_ms)}.`
    : `Checked ${rangeStamp(info.now_ms)}. ${info.ready ? "Storage is accepting work." : "Monitoring is not ready to accept work; inspect the diagnostics below."}${info.configuration.status === "rejected" ? " Configuration reload was rejected; the previous valid settings remain active." : ""}`;
  const cards = [
    ["Readiness · last check", info.ready ? "Ready" : "Unavailable", info.writer.stalled ? "Writer acknowledgement overdue" : `SQL writer ${info.writer.alive ? "running" : "stopped"}`],
    ["Ingestion · this process", number(info.ingestion.accepted), `${number(info.ingestion.duplicates)} retries deduplicated · ${number(info.ingestion.rejected)} rejected`],
    ["History retained", storage.available && storage.oldest_at_ms ? rangeStamp(storage.oldest_at_ms) : "No metric history", storage.available ? `${number(storage.evicted_snapshots)} old observations evicted` : "Storage check failed"],
    ["Runtime", `${numeric(info.process.uptime_seconds / 3600, 1)}h`, `BoboWatch ${info.process.version} · started ${rangeStamp(info.process.started_at_ms)}`],
  ];
  $("operations-summary").replaceChildren(...cards.map(([title,value,detail]) => append(el("div"),el("small",title),el("strong",value),el("p",detail))));
  $("storage-meter").hidden = capacity?.ratio == null;
  $("storage-meter").value = Math.min(1, capacity?.ratio || 0);
  $("storage-meter").setAttribute("aria-valuetext", capacity ? `${bytes(capacity.used)} used${capacity.quota ? ` of ${bytes(capacity.quota)}` : ", no quota"}` : "Storage unavailable");
  $("storage-capacity").textContent = capacity ? `${bytes(capacity.used)} used${capacity.quota ? ` / ${bytes(capacity.quota)} quota (${numeric(capacity.ratio * 100)}%) · ${bytes(capacity.headroom)} headroom after reserve` : " · no configured quota"}` : "Storage unavailable";
  $("storage-capacity").className = capacity?.warning ? "error-number" : "";
  const when = stamp => stamp ? rangeStamp(stamp) : "None in this process";
  const details = [
    ["Configuration", `${info.configuration.status}${info.configuration.error_type ? ` (${info.configuration.error_type})` : ""} · checked ${when(info.configuration.last_checked_at_ms)}`],
    ["Writer queue", `${info.writer.queue_depth} / ${info.writer.queue_capacity} queued · ${info.writer.accepting ? "accepting" : "stopping"}${info.writer.stalled ? " · overdue acknowledgement; work may still commit" : ""}`],
    ["Storage maintenance", info.maintenance.error_type ? `${info.maintenance.error_type} · last error ${when(info.maintenance.last_error_at_ms)}` : `Last success ${when(info.maintenance.last_success_at_ms)}`],
    ["Last accepted ingestion", when(info.ingestion.last_accepted_at_ms)],
    ["Last rejected ingestion", `${when(info.ingestion.last_rejected_at_ms)}${info.ingestion.last_status ? ` · last ingestion HTTP ${info.ingestion.last_status}` : ""}`],
    ["History epoch", storage.available ? storage.epoch : "Unavailable"],
    ["History policy", storage.available ? storage.ram_only ? "RAM only · lost on reboot, power loss or volume release" : "Disk-backed SQL runtime" : "Unavailable"],
    ["Physical files", storage.available ? `${bytes(storage.main_bytes)} database · ${bytes(storage.wal_bytes)} WAL · ${bytes(storage.shm_bytes)} shared memory` : "Unavailable"],
    ["Reusable pages / reserve", storage.available ? `${bytes(storage.reusable_bytes)} reusable · ${bytes(storage.reserve_bytes)} reserved` : "Unavailable"],
    ["Volume free / total", storage.available ? `${bytes(storage.volume_free_bytes)} / ${bytes(storage.volume_bytes)}` : "Unavailable"],
    ["Newest retained metric", storage.available ? when(storage.newest_at_ms) : "Unavailable"],
    ["Quota enforcement", storage.available ? storage.quota_applied ? "Within quota" : "Pending reclamation" : "Unavailable"],
  ];
  $("operations-details").replaceChildren(...details.flatMap(([key,value]) => [el("dt",key),el("dd",value)]));
  if (state.data) {
    const now = telemetryNow();
    const rows = state.data.sites.flatMap(s => s.replicas).map(r => {
      const health = el("td", BoboWatchOperations.status(r,now,state.data.stale_seconds));
      health.dataset.telemetryReplica = r.id;
      return append(el("tr"),el("td",r.id),health,
        el("td",r.observed_at_ms ? rangeStamp(r.observed_at_ms) : "Not observed"),
        el("td",r.received_at_ms ? rangeStamp(r.received_at_ms) : "Not received"),
        el("td",r.received_at_ms != null && r.observed_at_ms != null ? `${numeric((r.received_at_ms-r.observed_at_ms)/1000,3)}s` : "—"),el("td",r.revision || "Unknown"));
    });
    $("telemetry-details").replaceChildren(table(["Replica","Telemetry","Observed","Received","Delivery clock delta","Revision"],rows));
  }
}
async function refreshOperations() {
  clearTimeout(operationsTimer);
  if (state.operationsBusy || (!state.token && !state.localAccess) || document.hidden) return;
  state.operationsBusy = true;
  $("refresh-operations").disabled = true;
  const session = state.session;
  try {
    const response = await fetch("/api/operations", {headers: state.token ? {Authorization:"Bearer " + state.token} : {}});
    if (!response.ok) throw new Error(`Runtime check unavailable (HTTP ${response.status}).`);
    const data = await response.json();
    if (session !== state.session) return;
    const priorEpoch = state.operations?.storage?.epoch || state.data?.storage?.epoch;
    if (priorEpoch && data.storage.available && data.storage.epoch !== priorEpoch) state.epochChanged = true;
    state.operations = data;
    state.operationsMono = performance.now();
    state.operationsError = "";
  } catch (error) {
    if (session !== state.session) return;
    state.operationsError = error.message;
  } finally {
    state.operationsBusy = false;
    if (session === state.session) {
      renderOperations();
      if (state.refreshSeconds && !document.hidden) operationsTimer = setTimeout(refreshOperations,30000);
    }
  }
}
function render() {
  const data = state.data;
  if (!data) return;
  $("dashboard").classList.remove("no-history");
  const metrics = data.http,
    points = data.charts,
    replicas = data.sites.flatMap((site) => site.replicas),
    active = replicas.filter(replica => !replica.retired),
    fresh = state.error
      ? 0
      : replicas.filter((r) => r.status === "fresh").length;
  $("demo-banner").hidden = !data.demo;
  $("connection").replaceWith(
    Object.assign(
      badge(
        state.error ? "Connection interrupted" : "Connected",
        state.error ? "warning" : "",
      ),
      { id: "connection" },
    ),
  );
  $("updated").textContent = `As of ${detailClock(data.now_ms)}`;
  $("time-range-button").title = `${rangeStamp(data.range_start_ms)} → ${rangeStamp(data.range_end_ms)} · ${timezone}`;
  $("range-status").textContent = `${rangeStamp(data.range_start_ms)} → ${rangeStamp(data.range_end_ms)} · ${timezone}${data.historical ? " · Range totals; end excluded" : ""}`;
  $("clear").hidden = !state.replica;
  const inventories = {project: data.projects, site: data.site_options, host: data.hosts};
  const selectedScope = ["project", "site", "host"].filter(id => state.appliedScope[id])
    .map(id => inventories[id].find(row => row.id === state.appliedScope[id])?.title || state.appliedScope[id]);
  if (state.appliedScope.replica) selectedScope.push(`Replica: ${state.appliedScope.replica}`);
  $("scope-label").textContent = selectedScope.join(" / ") || "All projects · all sites · all hosts";
  $("clear-scope").hidden = !selectedScope.length;
  $("scope-meta").replaceChildren(
    el("div", `${data.sites.length} sites · ${replicas.length} replicas`),
    el(
      "div",
      `${number(metrics.snapshot_count)} observations · ${points.length} chart points`,
    ),
  );
  const coverage = !metrics.snapshot_count && data.historical
    ? "No retained metric snapshots in this range."
    : data.retained?.oldest_snapshot_at_ms > data.range_start_ms
      ? "Only retained history is shown; this range starts before the oldest available observation."
      : "";
  const issues = state.error ||
    (coverage || (!data.historical && fresh < active.length
      ? "Some expected replicas have no current telemetry. Missing values stay unknown." : ""));
  $("notice").hidden = !issues;
  $("notice").textContent = issues;
  $("summary").replaceChildren(
    kpi(
      data.historical ? "Requests observed" : "Request rate",
      data.historical ? number(metrics.requests) : state.error ? "—" : numeric(data.current.rps),
      data.historical ? "requests" : "req/s",
      `${number(metrics.requests)} requests in window`,
      (p) => p.rps,
      colors.teal,
      "↗",
    ),
    kpi(
      "p95 response time",
      latency(metrics.latency.p95),
      "",
      "Conservative histogram bound",
      (p) => pValue(p.p95),
      colors.purple,
      "◷",
    ),
    kpi(
      "Server error rate",
      metrics.requests
        ? `${numeric((metrics.errors / metrics.requests) * 100, 2)}%`
        : "—",
      "",
      `${number(metrics.errors)} responses with 5xx`,
      (p) => p.server_error_pct,
      colors.red,
      "⌁",
    ),
    kpi(
      data.historical ? "Observations" : "Requests in flight",
      data.historical ? number(metrics.snapshot_count) : state.error ? "—" : number(data.current.in_flight),
      data.historical ? "samples" : "active",
      data.historical ? "Recorded in selected range" : `${fresh} / ${active.length} active replicas reporting`,
      (p) => p.in_flight,
      colors.blue,
      "⇄",
    ),
  );
  $("secondary-summary").replaceChildren(
    kpi("Mean response time", numeric(metrics.mean_ms, 1), "ms",
      "Weighted by completed requests", p => p.mean_ms, colors.amber, "◷"),
    kpi("Responses over 1s", metrics.slow_request_pct === null ? "—" : `${numeric(metrics.slow_request_pct, 1)}%`, "",
      `${number(metrics.slow_requests)} / ${number(metrics.requests)} requests`,
      p => p.slow_request_pct, colors.purple, "⌁"),
    kpi("Client error rate", percent(metrics.status_classes?.[3], metrics.http_requests), "",
      `${metrics.status_classes ? number(metrics.status_classes[3]) : "—"} responses with 4xx`,
      p => p.client_error_pct, colors.amber, "↙"),
    kpi("Mean response body", bytes(metrics.mean_response_bytes), "",
      `${number(metrics.sized_requests)} / ${number(metrics.requests)} requests measured`,
      p => p.mean_response_bytes, colors.blue, "⇄"),
  );
  const investigate = (label, sort) => {
    const button = el("button", label, "investigate");
    button.type = "button";
    button.addEventListener("click", () => {
      $("route-sort").value = sort;
      $("route-search").value = "";
      $("route-minimum").value = "1";
      state.routePage = 0;
      routeTable();
      $("routes").scrollIntoView({ block: "start", behavior: "smooth" });
      $("route-sort").focus({ preventScroll: true });
    });
    return button;
  };
  $("summary").children[2].append(investigate("Explore server errors →", "errors"));
  $("secondary-summary").children[1].append(investigate("Explore slow endpoints →", "mean"));
  $("coverage-note").textContent = metrics.requests
    ? `Status & endpoints: ${percent(metrics.http_requests, metrics.requests)} · Body sizes: ${percent(metrics.sized_requests, metrics.requests)} · Measured bodies: ${metrics.sized_requests ? `${bytes(metrics.bytes_sent)} out / ${bytes(metrics.bytes_received)} in` : "unavailable"}. Gaps are unknown; body sizes exclude headers.`
    : "No completed requests in this window. Latency, percentages and body averages remain unknown.";
  $("telemetry-status").textContent = state.error ? "Updates unavailable"
    : data.historical ? `${replicas.filter(r => r.http.snapshot_count).length}/${replicas.length} replicas recorded`
    : `${fresh}/${active.length} active replicas reporting`;
  $("telemetry-status").className = `pill ${state.error || (!data.historical && fresh < active.length) ? "warning" : "neutral"}`;
  const trafficCounts = $("traffic-view").value === "count";
  $("traffic-title").textContent = trafficCounts ? "Request volume" : "Request rate";
  const traffic = [
    { name: "All selected sites", color: colors.teal, observed: trafficCounts, get: (p) => trafficCounts ? p.requests : p.rps },
  ];
  if (data.sites.length > 1)
    data.sites.forEach((site, i) =>
      traffic.push({
        name: site.title,
        color: [colors.blue, colors.purple, colors.amber][i % 3],
        get: (p) => trafficCounts ? p.requests : p.rps,
        points: site.charts,
        observed: trafficCounts,
      }),
    );
  lineChart("traffic-chart", trafficCounts ? "Requests per chart bucket" : "Request rate over time", traffic, (v, tick) =>
    tick ? numeric(v, 0) : trafficCounts ? `${number(v)} requests` : `${numeric(v, 2)} req/s`,
  );
  $("traffic-value").textContent = data.historical || trafficCounts
    ? `${number(metrics.requests)} requests` : state.error ? "—" : `${numeric(data.current.rps)} req/s`;
  lineChart(
    "latency-chart",
    "Response time percentile bounds",
    [
      {
        name: "p50",
        observed: true,
        caption: latency(metrics.latency.p50),
        color: colors.teal,
        get: (p) => (pValue(p.p50) === null ? null : pValue(p.p50) / 1000),
      },
      {
        name: "p95",
        observed: true,
        caption: latency(metrics.latency.p95),
        color: colors.blue,
        get: (p) => (pValue(p.p95) === null ? null : pValue(p.p95) / 1000),
      },
      {
        name: "p99",
        observed: true,
        caption: latency(metrics.latency.p99),
        color: colors.purple,
        get: (p) => (pValue(p.p99) === null ? null : pValue(p.p99) / 1000),
      },
      {
        name: "Mean", caption: metrics.mean_ms === null ? "—" : `${numeric(metrics.mean_ms)} ms`,
        observed: true,
        color: colors.amber, get: p => p.mean_ms === null ? null : p.mean_ms / 1000,
        format: v => `${numeric(v, 3)} s`,
      },
    ],
    (v, tick) => (tick ? numeric(v, 2) : `≤${numeric(v, 3)} s`),
  );
  const errorCounts = $("error-view").value === "count";
  lineChart(
    "errors-chart",
    "4xx and 5xx response error share",
    [
      {
        name: "4xx client errors",
        observed: true,
        color: colors.amber,
        get: (p) => errorCounts ? p.client_errors : p.client_error_pct,
      },
      {
        name: "5xx server errors",
        observed: true,
        color: colors.red,
        get: (p) => errorCounts ? p.errors : p.server_error_pct,
      },
    ],
    (v, tick) => errorCounts ? tick ? number(v) : `${number(v)} responses` : `${numeric(v, 1)}%`,
  );
  lineChart("slow-chart", "Share of completed requests over one second", [
    {name: ">1 second", color: colors.purple, observed: true, get: p => p.slow_request_pct},
  ], v => `${numeric(v, 1)}%`);
  lineChart("payload-chart", "Mean body size among measured requests", [
    {name: "Response body", color: colors.teal, observed: true, get: p => p.mean_response_bytes === null ? null : p.mean_response_bytes / 1024},
    {name: "Request body", color: colors.blue, observed: true, get: p => p.mean_request_bytes === null ? null : p.mean_request_bytes / 1024},
  ], (v, tick) => tick ? numeric(v, 1) : `${numeric(v, 2)} KiB`);
  lineChart(
    "concurrency-chart",
    "Sampled requests currently in flight",
    [{ name: "Active requests", color: colors.blue, get: (p) => p.in_flight }],
    (v, tick) => (tick ? number(v) : `${number(v)} active`),
  );
  lineChart(
    "throughput-chart",
    "HTTP body throughput",
    [
      {
        name: "Response bodies",
        color: colors.teal,
        get: (p) =>
          p.bytes_sent_per_sec === null ? null : p.bytes_sent_per_sec / 1024,
      },
      {
        name: "Request bodies",
        color: colors.blue,
        get: (p) =>
          p.bytes_received_per_sec === null
            ? null
            : p.bytes_received_per_sec / 1024,
      },
    ],
    (v, tick) => (tick ? numeric(v, 0) : `${numeric(v, 1)} KiB/s`),
  );
  serviceComparison();
  latencyHeatmap();
  histogram();
  responseMix();
  routeTable();
  $("replica-count").textContent =
    data.historical
      ? `${replicas.filter(replica => replica.http.snapshot_count).length} / ${replicas.length} with observations · select a replica to inspect`
      : `${fresh} / ${active.length} active reporting · select a replica to inspect`;
  $("sites").replaceChildren(
    ...(data.sites.length
      ? data.sites.map(siteCard)
      : [
          el(
            "div",
            "No sites in this scope. Adjust the filters or configure your inventory.",
            "empty",
          ),
        ]),
  );
  renderEvents();
  $("export-view").disabled = false;
  $("data-meta").textContent =
    `${number(metrics.snapshot_count)} source observations · ${numeric(data.bucket_seconds, 3)}s chart groups · all matching records included. Partial counts are observed values; unavailable rates remain gaps.`;
  renderRangeNavigation();
  renderFreshness();
  renderOperations();
}
async function refresh() {
  if (!state.token && !state.localAccess) return false;
  if (state.workspaceInvalid) {
    $("login").hidden = true;
    $("dashboard").hidden = false;
    if (!state.data) $("dashboard").classList.add("no-history");
    refreshOperations();
    return false;
  }
  clearTimeout(timer);
  state.nextRefresh = null;
  state.controller?.abort();
  state.busy = true;
  $("refresh").disabled = true;
  $("cancel-query").hidden = false;
  const session = state.session;
  const controller = new AbortController();
  state.controller = controller;
  const requestedView = viewState();
  recordLocation();
  refreshOperations();
  const started = performance.now();
  const progress = setInterval(() => {
    if (state.controller !== controller) return;
    $("query-status").textContent = `Reading all matching history… ${Math.floor((performance.now() - started) / 1000)}s elapsed`;
  }, 5000);
  $("query-status").textContent = "Reading all matching history…";
  $("dashboard").setAttribute("aria-busy", "true");
  if (!state.data && state.localAccess) {
    $("login").hidden = true;
    $("dashboard").hidden = false;
    $("dashboard").classList.add("no-history");
    $("logout").hidden = state.localAccess;
    $("scope-label").textContent = "Loading selected range";
  }
  try {
    const selected = ranges.resolve($("window").value, Date.now(), state.customRange);
    const query = new URLSearchParams(selected.absolute
      ? { start_ms: selected.start_ms, end_ms: selected.end_ms }
      : { window: selected.window });
    if ($("chart-detail").value !== "auto") query.set("bucket_ms", $("chart-detail").value);
    const requestedScope = { replica: state.replica };
    for (const key of ["project", "site", "host"])
      if ($(key).value) {
        requestedScope[key] = $(key).value;
        query.set(key, $(key).value);
      }
    if (state.replica) query.set("replica", state.replica);
    const response = await fetch("/api/overview?" + query, {
      headers: state.token ? { Authorization: "Bearer " + state.token } : {},
      signal: controller.signal,
    });
    if (session !== state.session || state.controller !== controller) return false;
    if (response.status === 401) {
      state.token = "";
      state.localAccess = false;
      state.data = null;
      clearTimeout(timer);
      showLogin();
    }
    if (!response.ok) throw new Error(response.status === 401
      ? "Admin token was not accepted."
      : `Updates unavailable (${response.status}). Displayed history is not live.`);
    const data = await response.json();
    if (session !== state.session || state.controller !== controller) return false;
    if (state.data?.storage?.epoch && data.storage?.epoch !== state.data.storage.epoch) state.epochChanged = true;
    state.data = data;
    state.loadedMono = started;
    state.appliedScope = requestedScope;
    state.appliedView = requestedView;
    state.localAccess = data.dashboard_access === "local";
    if (state.localAccess) state.token = "";
    state.error = "";
    options("project", data.projects, "All projects");
    options("host", data.hosts, "All hosts");
    options(
      "site",
      (data.site_options || []).filter(
        (site) => !$("project").value || site.project_id === $("project").value,
      ),
      "All sites",
    );
    $("login").hidden = true;
    $("dashboard").hidden = false;
    $("logout").hidden = state.localAccess;
    render();
    return true;
  } catch (error) {
    if (session !== state.session || state.controller !== controller) return false;
    state.error =
      error.name === "AbortError"
        ? "This query was cancelled. Displayed values are from the last successful range."
        : error.message;
    if (state.data) render();
    else if (state.token || state.localAccess) {
      $("login").hidden = true;
      $("dashboard").hidden = false;
      $("dashboard").classList.add("no-history");
      $("logout").hidden = state.localAccess;
      $("connection").textContent = "Updates unavailable";
      $("notice").textContent = state.error;
      $("notice").hidden = false;
      $("scope-label").textContent = "No range loaded";
    } else $("login-error").textContent = state.error;
    return false;
  } finally {
    clearInterval(progress);
    if (state.controller !== controller) return false;
    $("query-status").textContent = "";
    $("dashboard").setAttribute("aria-busy", "false");
    state.busy = false;
    $("refresh").disabled = false;
    $("cancel-query").hidden = true;
    state.controller = null;
    scheduleRefresh();
    renderFreshness();
  }
}
function showLogin() {
  if ($("time-picker").open) closeTimePicker();
  $("login").hidden = false;
  $("dashboard").hidden = true;
  $("logout").hidden = true;
  $("login-form").hidden = state.localAccess;
  $("login-hint").hidden = state.localAccess;
  $("login-title").textContent = state.localAccess
    ? "Your local monitoring dashboard"
    : "A clearer picture of every request.";
  $("login-description").textContent = state.localAccess
    ? "Connecting to monitoring history. No login is required on your local network."
    : "Open your HTTP monitoring workspace with your admin token.";
}
async function connect() {
  const controller = new AbortController();
  $("connection").textContent = "Connecting";
  $("retry-connect").hidden = true;
  try {
    const response = await fetch("/api/access", { signal: controller.signal });
    if (!response.ok) throw new Error("Dashboard connection unavailable. Reload to retry.");
    const access = await response.json();
    state.localAccess = access.dashboard_access === "local";
    showLogin();
    if (state.localAccess) {
      await refresh();
    } else $("connection").textContent = "Private dashboard";
  } catch (error) {
    showLogin();
    $("login-form").hidden = true;
    $("login-hint").hidden = true;
    $("login-title").textContent = "Dashboard unavailable";
    $("login-description").textContent = "Check that BoboWatch is running, then reload this page.";
    $("login-error").textContent = error.name === "AbortError"
      ? "Dashboard connection timed out. Reload to retry."
      : error.message;
    $("retry-connect").hidden = false;
  }
}
$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  state.session++;
  state.token = $("token").value.trim();
  $("token").value = "";
  const button = $("login-form").querySelector("button");
  button.disabled = true;
  try {
    if (!(await refresh()) && !state.data && $("dashboard").hidden) {
      state.token = "";
      scheduleRefresh();
    }
  } finally {
    button.disabled = false;
  }
});
$("logout").addEventListener("click", () => {
  state.session++;
  state.controller?.abort();
  state.token = "";
  state.data = null;
  state.operations = null;
  state.replica = "";
  state.error = "";
  state.pending = false;
  clearTimeout(timer);
  clearTimeout(operationsTimer);
  showLogin();
  $("login-error").textContent = "";
  $("token").focus();
  $("connection").textContent = "Private dashboard";
  $("updated").textContent = "Not connected";
});
for (const id of ["project", "site", "host", "window", "chart-detail"])
  $(id).addEventListener("change", () => {
    state.routePage = 0;
    state.eventPage = 0;
    if (["project", "site", "host"].includes(id)) state.replica = "";
    if (id === "project") $("site").value = "";
    if (id === "window") syncTimeControls();
    if (id === "window" || id === "chart-detail") savePreferences();
    refresh();
  });
$("custom-range").addEventListener("submit", event => {
  event.preventDefault();
  try {
    const chosen = ranges.resolve("custom", Date.now(), {
      start_ms: ranges.parseLocalInput($("range-start").value),
      end_ms: ranges.parseLocalInput($("range-end").value),
    });
    state.customRange = { start_ms: chosen.start_ms, end_ms: chosen.end_ms };
    $("window").value = "custom";
    state.routePage = 0;
    state.eventPage = 0;
    $("range-error").hidden = true;
    syncTimeControls();
    closeTimePicker();
    savePreferences();
    refresh();
  } catch (error) {
    $("range-error").textContent = error.message;
    $("range-error").hidden = false;
  }
});
$("auto-refresh").addEventListener("change", () => {
  state.refreshSeconds = Number($("auto-refresh").value);
  syncTimeControls();
  savePreferences();
  scheduleRefresh();
  recordLocation(true);
  clearTimeout(operationsTimer);
  if (state.refreshSeconds) refreshOperations();
  renderFreshness();
});
document.addEventListener("visibilitychange", () => {
  clearTimeout(timer);
  clearTimeout(operationsTimer);
  state.nextRefresh = null;
  if (!document.hidden && state.refreshSeconds && (state.token || state.localAccess))
    refresh();
  renderFreshness();
});
$("refresh").addEventListener("click", refresh);
$("cancel-query").addEventListener("click", () => state.controller?.abort());
$("retry-connect").addEventListener("click", connect);
$("refresh-operations").addEventListener("click", refreshOperations);
$("reset-view").addEventListener("click", () => {
  history.replaceState(null,"",location.pathname + location.hash);
  state.workspaceInvalid = false;
  $("reset-view").hidden = true;
  for (const id of ["project","site","host"]) $(id).value = "";
  state.replica = "";
  $("window").value = "300";
  syncTimeControls();
  viewMessage("");
  refresh();
});
for (const [id,direction] of [["previous-range",-1],["next-range",1]]) $(id).addEventListener("click", () => {
  try { applyExactRange(BoboWatchWorkspace.shiftRange(state.data,direction)); }
  catch (error) { viewMessage(error.message); }
});
$("live-range").addEventListener("click", () => {
  $("window").value = "300";
  syncTimeControls(); savePreferences(); refresh();
});
$("copy-view").addEventListener("click", async () => {
  if (!state.data) return;
  try {
    const query = BoboWatchWorkspace.encode({...viewState(),
      ...Object.fromEntries(["project","site","host","replica"].map(key => [key,state.appliedScope[key] || ""])),
      detail:state.appliedView.detail,
      routeQuery:$("route-search").value, eventQuery:$("event-search").value,
      routeSort:$("route-sort").value, routeMinimum:$("route-minimum").value, routeSize:$("route-page-size").value,
      eventSeverity:$("event-severity").value, eventSize:$("event-page-size").value},
      {fixedRange:{range_start_ms:state.data.range_start_ms,range_end_ms:state.data.range_end_ms + (state.data.historical ? 0 : 1)}});
    const url = new URL(location.href); url.search = query;
    $("view-link").value = url.href;
    $("link-fallback").hidden = false;
    try {
      await navigator.clipboard.writeText(url.href);
      viewMessage("View link copied with fixed dates and the displayed scope. Links contain no credentials. Retention may remove these observations later.");
    } catch {
      $("view-link").focus(); $("view-link").select();
      viewMessage("Copy the view link below. Its dates and displayed scope are fixed; retained data may expire later.");
    }
  } catch (error) { viewMessage(error.message); }
});
$("close-view-link").addEventListener("click", () => { $("link-fallback").hidden = true; $("copy-view").focus(); });
addEventListener("popstate", () => {
  state.controller?.abort();
  if (applyLocation()) refresh();
});
addEventListener("offline", renderFreshness);
addEventListener("online", () => { if (state.localAccess || state.token) refresh(); else connect(); });
$("export-view").addEventListener("click", () => {
  if (!state.data) return;
  const blob = new Blob([JSON.stringify({format: "bobowatch.overview.v1", exported_at_ms: Date.now(),
    scope: state.appliedScope, overview: state.data}, null, 2)], {type: "application/json"});
  const url = URL.createObjectURL(blob), link = el("a");
  link.href = url;
  link.download = `bobowatch-${new Date(state.data.range_end_ms).toISOString().replaceAll(":", "-")}.json`;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
for (const id of ["event-search", "event-severity", "event-page-size"])
  $(id).addEventListener(id === "event-search" ? "input" : "change", () => {
    state.eventPage = 0; renderEvents();
    recordLocation(true);
  });
for (const [id, delta] of [["event-previous", -1], ["event-next", 1]])
  $(id).addEventListener("click", () => { state.eventPage += delta; renderEvents(); });
$("clear").addEventListener("click", () => {
  state.replica = "";
  state.routePage = 0;
  refresh();
});
$("clear-scope").addEventListener("click", () => {
  for (const id of ["project", "site", "host"]) $(id).value = "";
  state.replica = "";
  state.routePage = 0;
  refresh();
});
$("heatmap-mode").addEventListener("change", () => { savePreferences(); recordLocation(true); if (state.data) latencyHeatmap(); });
$("service-sort").addEventListener("change", () => { savePreferences(); recordLocation(true); if (state.data) { serviceComparison(); renderFreshness(); } });
for (const id of ["traffic-view", "error-view"]) $(id).addEventListener("change", () => {
  savePreferences();
  recordLocation(true);
  render();
});
for (const id of ["route-search", "route-sort", "route-minimum", "route-page-size"])
  $(id).addEventListener(id === "route-search" ? "input" : "change", () => {
    state.routePage = 0;
    if (state.data) routeTable();
    recordLocation(true);
  });
for (const [id, delta] of [["route-previous", -1], ["route-next", 1]])
  $(id).addEventListener("click", () => {
    state.routePage += delta;
    routeTable();
  });
$("dashboard").addEventListener("click", (event) => {
  const incident = event.target.closest("[data-event-at]");
  if (incident) {
    const at = Number(incident.dataset.eventAt);
    applyExactRange({start_ms:Math.max(0,at - 300000),end_ms:Math.min(Date.now(),at + 300000)});
    $("performance").scrollIntoView({block:"start",behavior:matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"});
    return;
  }
  const siteButton = event.target.closest("[data-site]");
  if (siteButton) {
    const site = state.data.site_options.find(site => site.id === siteButton.dataset.site);
    if (!site) return;
    $("project").value = site.project_id;
    options("site", state.data.site_options.filter(s => s.project_id === site.project_id), "All sites");
    $("site").value = site.id;
    state.replica = "";
    state.routePage = 0;
    refresh();
    $("overview").scrollIntoView({block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"});
    $("site").focus({preventScroll: true});
    return;
  }
  const button = event.target.closest("[data-replica]");
  if (button) {
    state.replica = button.dataset.replica;
    state.routePage = 0;
    refresh();
    $("overview").scrollIntoView({block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"});
    $("clear").hidden = false;
    $("clear").focus({preventScroll: true});
  }
});
const sectionLinks = [...document.querySelectorAll(".sidebar nav a")];
let navigationFrame;
addEventListener("scroll", () => {
  if (navigationFrame) return;
  navigationFrame = requestAnimationFrame(() => {
    navigationFrame = null;
    const active = sectionLinks.filter(link => document.querySelector(link.hash).getBoundingClientRect().top <= 120).at(-1) || sectionLinks[0];
    for (const link of sectionLinks) {
      link.classList.toggle("nav-active", link === active);
      if (link === active) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    }
  });
}, {passive: true});
let heatmapWidth = 0, heatmapFrame;
new ResizeObserver(entries => {
  const width = Math.round(entries[0].contentRect.width);
  if (!width || width === heatmapWidth) return;
  heatmapWidth = width;
  cancelAnimationFrame(heatmapFrame);
  if (state.data) heatmapFrame = requestAnimationFrame(latencyHeatmap);
}).observe($("duration-heatmap"));
setupTimePicker();
restorePreferences();
applyLocation();
setInterval(() => {
  if (!document.hidden) {
    renderFreshness();
    if (state.operations && performance.now() - state.operationsMono > 90000 && !state.operationsError) {
      $("operations-message").textContent = `Runtime diagnostics were last checked ${rangeStamp(state.operations.now_ms)}. Use Check now to confirm current readiness.`;
    }
  }
},1000);
connect();
