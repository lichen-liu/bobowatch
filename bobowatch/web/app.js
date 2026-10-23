"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  token: "",
  data: null,
  replica: "",
  busy: false,
  pending: false,
  error: "",
  session: 0,
  controller: null,
  hiddenSeries: new Map(),
};
let timer;
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
    start = state.data.now_ms - state.data.window_seconds * 1000,
    end = state.data.now_ms;
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
      clock(start + ((end - start) * i) / 4),
    );
  shown.forEach((line, index) => {
    let d = "",
      active = false,
      previous = null;
    for (const point of line.points || points) {
      const value = line.get(point);
      if (point.partial || value === null || !Number.isFinite(value)) {
        active = false;
        previous = null;
        continue;
      }
      if (
        previous !== null &&
        point.at_ms - previous > state.data.bucket_seconds * 1800
      )
        active = false;
      d += `${active ? "L" : "M"}${x(point.at_ms).toFixed(2)},${y(value).toFixed(2)} `;
      active = true;
      previous = point.at_ms;
    }
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
      el("strong", detailClock(at) + (point.partial ? " · partial" : "")),
    );
    shown.forEach((line, i) => {
      const sample = (line.points || points).find((p) => p.at_ms === at),
        value = sample && !sample.partial ? line.get(sample) : null;
      const row = append(
        el("div"),
        el("span", line.name),
        el("b", value === null ? "No data" : format(value, false)),
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
  headers.forEach((name, i) => head.append(el("th", name, i ? "numeric" : "")));
  node.append(append(el("thead"), head), append(el("tbody"), ...rows));
  return node;
}
function routeTable() {
  const host = $("route-table");
  host.replaceChildren();
  const routes = state.data.http.routes;
  if (!routes.length) {
    host.append(
      el(
        "p",
        "No endpoint observations. Register bounded route templates in the adapter.",
        "empty",
      ),
    );
    return;
  }
  host.append(
    table(
      ["Endpoint / site", "Requests", "5xx", "Mean time"],
      routes
        .filter((r) => r.requests)
        .slice(0, 8)
        .map((route) => {
          const first = append(
            el("td"),
            el("span", route.route, "endpoint"),
            el("small", route.site || ""),
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
          );
        }),
    ),
  );
}
function siteCard(site) {
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
  node.append(
    append(
      el("div", undefined, "site-top"),
      title,
      badge(
        state.error
          ? "Updates unavailable"
          : fresh === site.replicas.length
            ? "Telemetry current"
            : `${fresh}/${site.replicas.length} reporting`,
        state.error || fresh < site.replicas.length ? "warning" : "",
      ),
    ),
  );
  const values = [
    [
      "Current traffic",
      site.current.rps === null || state.error
        ? "—"
        : numeric(site.current.rps),
      "req/s",
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
      el("small", `${replica.host_id} · ${age(replica.observed_at_ms)}`),
    );
    return append(
      el("tr"),
      identity,
      el("td", state.error ? "—" : numeric(replica.current.rps), "numeric"),
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
        state.error ? "—" : number(replica.current.in_flight),
        "numeric",
      ),
      append(
        el("td"),
        badge(
          status,
          state.error || replica.status !== "fresh" ? "warning" : "",
        ),
      ),
    );
  });
  node.append(
    append(
      el("div", undefined, "table-scroll"),
      table(["Replica", "req/s", "p95", "5xx", "Active", "Telemetry"], rows),
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
  $("updated").textContent = `Updated ${clock(data.now_ms)}`;
  $("clear").hidden = !state.replica;
  $("scope-meta").replaceChildren(
    el("div", `${data.sites.length} sites · ${replicas.length} replicas`),
    el(
      "div",
      `${number(metrics.snapshot_count)} observations · ${points.length} chart points`,
    ),
  );
  const issues =
    state.error ||
    (data.sites.some((s) => s.history_truncated)
      ? "This window exceeds the 4,000-snapshot query budget. Charts and totals cover only the newest observations; narrow the window."
      : fresh < replicas.length
        ? "Some expected replicas have no current telemetry. Missing values stay unknown."
        : "");
  $("notice").hidden = !issues;
  $("notice").textContent = issues;
  $("summary").replaceChildren(
    kpi(
      "Request rate",
      state.error ? "—" : numeric(data.current.rps),
      "req/s",
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
      "Requests in flight",
      state.error ? "—" : number(data.current.in_flight),
      "active",
      `${fresh} / ${replicas.length} replicas reporting`,
      (p) => p.in_flight,
      colors.blue,
      "⇄",
    ),
  );
  const traffic = [
    { name: "All selected sites", color: colors.teal, get: (p) => p.rps },
  ];
  if (data.sites.length > 1)
    data.sites.forEach((site, i) =>
      traffic.push({
        name: site.title,
        color: [colors.blue, colors.purple, colors.amber][i % 3],
        get: (p) => p.rps,
        points: site.charts,
      }),
    );
  lineChart("traffic-chart", "Request rate over time", traffic, (v, tick) =>
    tick ? numeric(v, 0) : `${numeric(v, 2)} req/s`,
  );
  $("traffic-value").textContent = state.error
    ? "—"
    : `${numeric(data.current.rps)} req/s`;
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
    ],
    (v, tick) => (tick ? numeric(v, 2) : `≤${numeric(v, 3)} s`),
  );
  lineChart(
    "errors-chart",
    "4xx and 5xx response error share",
    [
      {
        name: "4xx client errors",
        color: colors.amber,
        get: (p) => p.client_error_pct,
      },
      {
        name: "5xx server errors",
        color: colors.red,
        get: (p) => p.server_error_pct,
      },
    ],
    (v) => `${numeric(v, 1)}%`,
  );
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
    `${fresh} / ${replicas.length} reporting · select a replica to inspect`;
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
  if (!state.token) return false;
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
    const query = new URLSearchParams({ window: $("window").value });
    for (const key of ["project", "site", "host"])
      if ($(key).value) query.set(key, $(key).value);
    if (state.replica) query.set("replica", state.replica);
    const response = await fetch("/api/overview?" + query, {
      headers: { Authorization: "Bearer " + state.token },
      signal: controller.signal,
    });
    if (!response.ok)
      throw new Error(
        response.status === 401
          ? "Admin token was not accepted."
          : `Updates unavailable (${response.status}). Displayed history is not live.`,
      );
    const data = await response.json();
    if (session !== state.session) return false;
    state.data = data;
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
    $("logout").hidden = false;
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
    if (state.pending && state.token) {
      state.pending = false;
      queueMicrotask(refresh);
    }
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
    if (await refresh()) {
      clearInterval(timer);
      timer = setInterval(refresh, 3000);
    } else if (!state.data) state.token = "";
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
  clearInterval(timer);
  $("dashboard").hidden = true;
  $("login").hidden = false;
  $("logout").hidden = true;
  $("login-error").textContent = "";
  $("token").focus();
  $("connection").textContent = "Private dashboard";
  $("updated").textContent = "Not connected";
});
for (const id of ["project", "site", "host", "window"])
  $(id).addEventListener("change", () => {
    state.replica = "";
    if (id === "project") $("site").value = "";
    refresh();
  });
$("refresh").addEventListener("click", refresh);
$("clear").addEventListener("click", () => {
  state.replica = "";
  refresh();
});
$("sites").addEventListener("click", (event) => {
  const button = event.target.closest("[data-replica]");
  if (button) {
    state.replica = button.dataset.replica;
    refresh();
  }
});
