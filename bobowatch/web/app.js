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
  appliedScope: {},
  hiddenSeries: new Map(),
};
let timer;
const preferenceKey = "bobowatch.dashboard.preferences.v1";
const ranges = BoboWatchRanges;
const timeWindows = new Set([...ranges.rolling, ...ranges.calendar, "custom"]);
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
  } catch {
    // Storage may be unavailable or contain invalid data. Keep the defaults.
  }
  state.refreshSeconds = Number($("auto-refresh").value);
  showCustomRange();
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
      }),
    );
  } catch {
    // The dashboard still works when browser storage is unavailable.
  }
}
function showCustomRange() {
  $("custom-range").hidden = $("window").value !== "custom";
  $("range-error").hidden = true;
  $("range-zone").textContent = `Times in ${timezone}. The end time is excluded.`;
  if ($("window").value !== "custom") return;
  if (!state.customRange) {
    const now = Date.now();
    state.customRange = {
      start_ms: state.data?.range_start_ms ?? now - 300000,
      end_ms: state.data?.range_end_ms ?? now,
    };
  }
  $("range-start").value = ranges.toLocalInput(state.customRange.start_ms);
  $("range-end").value = ranges.toLocalInput(state.customRange.end_ms);
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
  if (
    state.busy || document.hidden || !state.refreshSeconds ||
    (!state.token && !state.localAccess)
  ) return;
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
  const strong = el("strong", value);
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
  host.replaceChildren();
  const width = Math.max(300, Math.min(800, host.clientWidth - 12));
  const svg = svgNode(title, `0 0 ${width} 176`);
  svg.setAttribute("tabindex", "0");
  const hidden = state.hiddenSeries.get(id) || new Set();
  const shown = series.filter((line) => !hidden.has(line.name));
  const points = state.data.charts,
    start = state.data.range_start_ms ?? state.data.now_ms - state.data.window_seconds * 1000,
    end = state.data.range_end_ms ?? state.data.now_ms;
  const all = shown
    .flatMap((line) =>
      (line.points || points).map((point) =>
        point.partial ? null : line.get(point),
      ),
    )
    .filter((value) => value !== null && Number.isFinite(value));
  const upper = boundScale(Math.max(0, ...all) * 1.12),
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
        "font-size": 8,
      },
      format(value, true),
    );
  }
  for (let i = 0; i < 5; i++)
    shape(
      svg,
      "text",
      {
        x: left + (i * (right - left)) / 4,
        y: 165,
        "text-anchor": i === 0 ? "start" : i === 4 ? "end" : "middle",
        fill: "#a0adb6",
        "font-size": 8,
      },
      axisClock(start + ((end - start) * i) / 4),
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
          cy: y(line.get(isolated)),
          r: 2,
          fill: line.color,
        });
      isolated = null;
    };
    for (const point of line.points || points) {
      const value = line.get(point);
      if (point.partial || value === null || !Number.isFinite(value)) {
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
  const markers = shown.map((line) =>
    shape(svg, "circle", {
      r: 3,
      fill: line.color,
      stroke: "#fff",
      "stroke-width": 1,
      visibility: "hidden",
    }),
  );
  let focused = Math.max(0, points.length - 1);
  function inspect(index) {
    if (!points.length) return;
    focused = Math.max(0, Math.min(points.length - 1, index));
    const point = points[focused],
      at = point.at_ms;
    crosshair.setAttribute("d", `M${x(at)} ${top}V${bottom}`);
    tooltip.replaceChildren(
      el("strong", (state.data.window_seconds > 86400 ? rangeStamp(at) : detailClock(at)) + (point.partial ? " · partial" : "")),
    );
    shown.forEach((line, i) => {
      const sample = (line.points || points).find((p) => p.at_ms === at),
        value = sample && !sample.partial ? line.get(sample) : null;
      const row = append(
        el("div"),
        el("span", line.name),
        el("b", value === null ? "No data" : line.format ? line.format(value) : format(value, false)),
      );
      tooltip.append(row);
      markers[i].setAttribute(
        "visibility",
        value === null ? "hidden" : "visible",
      );
      if (value !== null) {
        markers[i].setAttribute("cx", x(at));
        markers[i].setAttribute("cy", y(value));
      }
    });
    tooltip.hidden = false;
  }
  svg.addEventListener("pointermove", (event) => {
    const rect = svg.getBoundingClientRect(),
      at =
        start +
        ((((event.clientX - rect.left) / rect.width) * width - left) /
          (right - left)) *
          (end - start);
    let index = 0,
      best = Infinity;
    points.forEach((p, i) => {
      if (Math.abs(p.at_ms - at) < best) {
        index = i;
        best = Math.abs(p.at_ms - at);
      }
    });
    inspect(index);
  });
  svg.addEventListener("pointerleave", () => {
    tooltip.hidden = true;
    crosshair.setAttribute("d", "");
    markers.forEach((m) => m.setAttribute("visibility", "hidden"));
  });
  svg.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      inspect(focused + (event.key === "ArrowLeft" ? -1 : 1));
    }
  });
  svg.addEventListener("focus", () => inspect(focused));
  svg.addEventListener("blur", () => {
    tooltip.hidden = true;
  });
  host.append(svg, tooltip);
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
          replacement?.focus();
        });
        return button;
      }),
    );
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
        "font-size": 8,
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
          "font-size": 7.5,
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
  node.append(append(el("thead"), head), append(el("tbody"), ...rows));
  return node;
}
function routeTable() {
  const host = $("route-table");
  host.replaceChildren();
  const routes = state.data.http.routes;
  const selection = BoboWatchMetrics.endpoints(routes, {
    query: $("route-search").value, sort: $("route-sort").value,
    minimum: Number($("route-minimum").value), page: state.routePage,
  });
  state.routePage = selection.page;
  const candidateCount = state.data.http.route_count;
  $("route-coverage").textContent = candidateCount > routes.length
    ? `${number(routes.length)} of ${number(candidateCount)} observed endpoints: the top 20 for each ranking. Narrow the site or time window to find more.`
    : `${number(candidateCount)} observed endpoints · ${percent(state.data.http.http_requests, state.data.http.requests)} request coverage. Totals include recorded intervals only.`;
  $("route-count").textContent = selection.total
    ? `${selection.page * 10 + 1}–${Math.min((selection.page + 1) * 10, selection.total)} of ${selection.total} matches`
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
          fill.style.width = `${(route.requests / Math.max(1, ...routes.map((r) => r.requests))) * 100}%`;
          bar.append(fill);
          first.append(bar);
          return append(
            el("tr"),
            first,
            el("td", number(route.requests), "numeric"),
            el("td", number(route.errors), route.errors ? "numeric error-number" : "numeric"),
            el(
              "td",
              `${numeric((route.errors / route.requests) * 100)}%`,
              "numeric",
            ),
            el(
              "td",
              `${numeric(route.duration_us / route.requests / 1000)} ms`,
              "numeric",
            ),
            el("td", `${numeric(route.duration_us / 1e6, 2)} s`, "numeric"),
          );
        }),
    ),
  );
}
function siteCard(site) {
  const historical = state.data.historical;
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
          : fresh === site.replicas.length
            ? "Telemetry current"
            : `${fresh}/${site.replicas.length} reporting`,
        state.error || (!historical && fresh < site.replicas.length) ? "warning" : "",
      ),
    ),
  );
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
          append(el("strong", value), el("em", unit)),
        ),
      ),
    ),
  );
  const rows = site.replicas.map((replica) => {
    const status = state.error
      ? "Updates unavailable"
      : historical ? (replica.http.snapshot_count ? "Recorded" : "No data")
      : {
          fresh: "Reporting",
          stale: "Stale",
          waiting: "Waiting",
          skewed: "Clock skew",
        }[replica.status];
    const button = el("button", `${replica.id} ↗`, "replica-button");
    button.dataset.replica = replica.id;
    const identity = append(
      el("td"),
      button,
      el("small", `${replica.host_id} · ${historical
        ? replica.range_observed_at_ms ? rangeStamp(replica.range_observed_at_ms) : "No observations"
        : age(replica.observed_at_ms)}`),
    );
    return append(
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
          state.error || (!historical && replica.status !== "fresh") ? "warning" : "",
        ),
      ),
    );
  });
  node.append(
    append(
      el("div", undefined, "table-scroll"),
      table(["Replica", historical ? "Requests" : "req/s", "p95", "5xx", historical ? "Samples" : "Active", historical ? "History" : "Telemetry"], rows),
    ),
    el(
      "p",
      `${number(site.requests)} requests observed · ${site.history_truncated ? "History capped; narrow the window" : site.partial ? "Partial intervals included" : "Selected time window"}`,
      "site-coverage",
    ),
  );
  return node;
}
function render() {
  const data = state.data;
  if (!data) return;
  const metrics = data.http,
    points = data.charts,
    replicas = data.sites.flatMap((site) => site.replicas),
    fresh = state.error
      ? 0
      : replicas.filter((r) => r.status === "fresh").length;
  $("demo-banner").hidden = !data.demo;
  const storage = data.storage;
  $("storage-status").textContent = storage
    ? `${storage.ram_only ? "RAM-only history" : "SQL history"} · ${(storage.used_bytes / 2 ** 20).toFixed(1)} MiB${storage.quota_bytes ? ` / ${(storage.quota_bytes / 2 ** 30).toFixed(2)} GiB quota` : ""} · epoch ${storage.epoch.slice(0, 8)} · ${number(storage.evicted_snapshots)} old observations evicted${storage.oldest_at_ms ? ` · oldest ${new Date(storage.oldest_at_ms).toLocaleString()}` : ""}`
    : "";
  $("connection").replaceWith(
    Object.assign(
      badge(
        state.error ? "Connection interrupted" : "Connected",
        state.error ? "warning" : "",
      ),
      { id: "connection" },
    ),
  );
  $("updated").textContent = `Updated ${detailClock(data.now_ms)}`;
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
    ? "No retained observations in this range."
    : storage?.oldest_at_ms > data.range_start_ms
      ? "Only retained history is shown; this range starts before the oldest available observation."
      : "";
  const issues = state.error ||
    (data.sites.some(site => site.history_truncated)
      ? "This window exceeds the 4,000-snapshot query budget. Charts and totals cover only the newest observations; narrow the window."
      : coverage || (!data.historical && fresh < replicas.length
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
      data.historical ? "Recorded in selected range" : `${fresh} / ${replicas.length} replicas reporting`,
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
    : `${fresh}/${replicas.length} replicas reporting`;
  $("telemetry-status").className = `pill ${state.error || (!data.historical && fresh < replicas.length) ? "warning" : "neutral"}`;
  const trafficCounts = $("traffic-view").value === "count";
  $("traffic-title").textContent = trafficCounts ? "Request volume" : "Request rate";
  const traffic = [
    { name: "All selected sites", color: colors.teal, get: (p) => trafficCounts ? p.requests : p.rps },
  ];
  if (data.sites.length > 1)
    data.sites.forEach((site, i) =>
      traffic.push({
        name: site.title,
        color: [colors.blue, colors.purple, colors.amber][i % 3],
        get: (p) => trafficCounts ? p.requests : p.rps,
        points: site.charts,
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
        caption: latency(metrics.latency.p50),
        color: colors.teal,
        get: (p) => (pValue(p.p50) === null ? null : pValue(p.p50) / 1000),
      },
      {
        name: "p95",
        caption: latency(metrics.latency.p95),
        color: colors.blue,
        get: (p) => (pValue(p.p95) === null ? null : pValue(p.p95) / 1000),
      },
      {
        name: "p99",
        caption: latency(metrics.latency.p99),
        color: colors.purple,
        get: (p) => (pValue(p.p99) === null ? null : pValue(p.p99) / 1000),
      },
      {
        name: "Mean", caption: metrics.mean_ms === null ? "—" : `${numeric(metrics.mean_ms)} ms`,
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
        color: colors.amber,
        get: (p) => errorCounts ? p.client_errors : p.client_error_pct,
      },
      {
        name: "5xx server errors",
        color: colors.red,
        get: (p) => errorCounts ? p.errors : p.server_error_pct,
      },
    ],
    (v, tick) => errorCounts ? tick ? number(v) : `${number(v)} responses` : `${numeric(v, 1)}%`,
  );
  lineChart("slow-chart", "Share of completed requests over one second", [
    {name: ">1 second", color: colors.purple, get: p => p.slow_request_pct},
  ], v => `${numeric(v, 1)}%`);
  lineChart("payload-chart", "Mean body size among measured requests", [
    {name: "Response body", color: colors.teal, get: p => p.mean_response_bytes === null ? null : p.mean_response_bytes / 1024},
    {name: "Request body", color: colors.blue, get: p => p.mean_request_bytes === null ? null : p.mean_request_bytes / 1024},
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
  histogram();
  responseMix();
  routeTable();
  $("replica-count").textContent =
    data.historical
      ? `${replicas.filter(replica => replica.http.snapshot_count).length} / ${replicas.length} with observations · select a replica to inspect`
      : `${fresh} / ${replicas.length} reporting · select a replica to inspect`;
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
  $("events").replaceChildren(
    ...(data.events.length
      ? data.events.slice(0, 12).map((event) => {
          const stamp = el("time", clock(event.occurred_at_ms));
          stamp.dateTime = new Date(event.occurred_at_ms).toISOString();
          return append(
            el("div", undefined, "event"),
            stamp,
            el("span", event.severity, `severity ${event.severity}`),
            el("div", event.message, "message"),
            el("span", `${event.site} / ${event.replica}`, "scope"),
          );
        })
      : [el("p", "No operational events in this window.", "empty")]),
  );
  $("data-meta").textContent =
    `${numeric(data.bucket_seconds)}s chart buckets · partial intervals remain gaps`;
}
async function refresh() {
  if (!state.token && !state.localAccess) return false;
  clearTimeout(timer);
  if (state.busy) {
    state.pending = true;
    return false;
  }
  state.busy = true;
  $("refresh").disabled = true;
  const session = state.session;
  const controller = new AbortController();
  state.controller = controller;
  const timeout = setTimeout(() => controller.abort(), 6000);
  try {
    const selected = ranges.resolve($("window").value, Date.now(), state.customRange);
    const query = new URLSearchParams(selected.absolute
      ? { start_ms: selected.start_ms, end_ms: selected.end_ms }
      : { window: selected.window });
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
    if (session !== state.session) return false;
    if (response.status === 401) {
      state.token = "";
      state.localAccess = false;
      state.data = null;
      clearTimeout(timer);
      showLogin();
    }
    if (!response.ok)
      throw new Error(
        response.status === 401
          ? "Admin token was not accepted."
          : `Updates unavailable (${response.status}). Displayed history is not live.`,
      );
    const data = await response.json();
    if (session !== state.session) return false;
    state.data = data;
    state.appliedScope = requestedScope;
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
    if (session !== state.session) return false;
    state.error =
      error.name === "AbortError"
        ? "Updates timed out. Displayed values are historical."
        : error.message;
    if (state.data) render();
    else $("login-error").textContent = state.error;
    return false;
  } finally {
    clearTimeout(timeout);
    state.busy = false;
    $("refresh").disabled = false;
    if (state.pending && (state.token || state.localAccess)) {
      state.pending = false;
      queueMicrotask(refresh);
    } else scheduleRefresh();
  }
}
function showLogin() {
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
  const timeout = setTimeout(() => controller.abort(), 6000);
  $("connection").textContent = "Connecting";
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
  } finally {
    clearTimeout(timeout);
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
    if (!(await refresh()) && !state.data) {
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
  state.replica = "";
  state.error = "";
  state.pending = false;
  clearTimeout(timer);
  showLogin();
  $("login-error").textContent = "";
  $("token").focus();
  $("connection").textContent = "Private dashboard";
  $("updated").textContent = "Not connected";
});
for (const id of ["project", "site", "host", "window"])
  $(id).addEventListener("change", () => {
    state.routePage = 0;
    if (id !== "window") state.replica = "";
    if (id === "project") $("site").value = "";
    if (id === "window") { showCustomRange(); savePreferences(); }
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
    $("range-error").hidden = true;
    savePreferences();
    refresh();
  } catch (error) {
    $("range-error").textContent = error.message;
    $("range-error").hidden = false;
  }
});
$("auto-refresh").addEventListener("change", () => {
  state.refreshSeconds = Number($("auto-refresh").value);
  savePreferences();
  scheduleRefresh();
});
document.addEventListener("visibilitychange", () => {
  clearTimeout(timer);
  if (!document.hidden && state.refreshSeconds && (state.token || state.localAccess))
    refresh();
});
$("refresh").addEventListener("click", refresh);
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
for (const id of ["traffic-view", "error-view"]) $(id).addEventListener("change", () => {
  savePreferences();
  render();
});
for (const id of ["route-search", "route-sort", "route-minimum"])
  $(id).addEventListener(id === "route-search" ? "input" : "change", () => {
    state.routePage = 0;
    if (state.data) routeTable();
  });
for (const [id, delta] of [["route-previous", -1], ["route-next", 1]])
  $(id).addEventListener("click", () => {
    state.routePage += delta;
    routeTable();
  });
$("dashboard").addEventListener("click", (event) => {
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
    $("overview").scrollIntoView({block: "start", behavior: "smooth"});
    $("site").focus({preventScroll: true});
    return;
  }
  const button = event.target.closest("[data-replica]");
  if (button) {
    state.replica = button.dataset.replica;
    state.routePage = 0;
    refresh();
    $("overview").scrollIntoView({block: "start", behavior: "smooth"});
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
restorePreferences();
connect();
