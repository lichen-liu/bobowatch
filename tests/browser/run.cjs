'use strict';
// Run only against the isolated fictional runtime created by run.py. No traces,
// screenshots or response-body files are written, including on failure.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium, webkit} = require('playwright');
const {runCharts} = require('./charts.cjs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
assert.equal(input.fictional, true);
assert.match(input.url, /^http:\/\/127\.0\.0\.1:\d+$/);

async function preview(page, name) {
  if (!input.output) return;
  fs.mkdirSync(input.output, {recursive: true});
  await page.screenshot({path: path.join(input.output, name + '.png')});
}
async function loaded(page) {
  await page.waitForFunction(() => !document.getElementById('refresh').disabled &&
    document.querySelectorAll('#summary > div').length === 4 &&
    !document.getElementById('dashboard').hidden);
}
async function update(page, action) {
  const pending = page.waitForResponse(response => response.url().includes('/api/overview?') && response.status() === 200);
  await action();
  const response = await pending;
  const data = await response.json();
  await loaded(page);
  return {query: new URL(response.url()).searchParams, data};
}
async function checkOperations(page) {
  const health = await page.request.get(input.url + '/health');
  assert.equal(health.status(), 200);
  assert.deepEqual(await health.json(), {status: 'ok'});
  const ready = await page.request.get(input.url + '/ready');
  assert.equal(ready.status(), 200);
  assert.deepEqual(await ready.json(), {status: 'ready'});
  await page.waitForFunction(() => document.querySelectorAll('#operations-summary > div').length >= 4);
  assert.match(await page.locator('#operations-summary').textContent(), /Ready/);
  const fail = route => route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({error: 'Fictional operations outage'})});
  await page.route('**/api/operations', fail);
  await page.locator('#refresh-operations').click();
  await page.waitForFunction(() => /previous check/.test(document.getElementById('operations-message').textContent));
  assert.match(await page.locator('#operations-summary').textContent(), /Ready/, 'previous diagnostic view survives failure');
  await page.unroute('**/api/operations', fail);
  await page.locator('#refresh-operations').click();
  await page.waitForFunction(() => /Checked/.test(document.getElementById('operations-message').textContent) && !/previous check/.test(document.getElementById('operations-message').textContent));
  const operations = await page.request.get(input.url + '/api/operations');
  assert.equal(operations.status(), 200);
  const result = await operations.json();
  assert.equal(result.ready, true);
  assert.equal(result.dashboard_access, 'local');
  assert.equal(result.writer.alive, true);
  assert.ok(!JSON.stringify(result).includes('token_hash'));
  assert.ok(!JSON.stringify(result).includes('admin.token'));
}
async function checkCompleteHistory(page) {
  const entire = await update(page, () => page.locator('#window').selectOption('all', {force: true}));
  assert.equal(entire.query.get('window'), 'all');
  assert.ok(entire.data.http.snapshot_count >= 4800, 'all retained history exceeds the old 4,000-observation budget');
  const query = new URLSearchParams({v: '1', window: 'custom', refresh: '0',
    start_ms: String(input.fixture.start_ms), end_ms: String(input.fixture.end_ms)});
  const automatic = await update(page, () => page.goto(input.url + '?' + query));
  const source = await update(page, () => page.locator('#chart-detail').selectOption('1'));
  const expected = input.fixture.expected;
  assert.equal(source.data.http.snapshot_count, expected.snapshots);
  assert.ok(source.data.charts.length >= 4800, 'source detail retains every distinct fixture timestamp');
  assert.equal(source.data.http.requests, expected.requests);
  assert.equal(source.data.http.errors, expected.errors);
  assert.equal(source.data.http.duration_us, expected.duration_us);
  assert.deepEqual(source.data.http.buckets, expected.buckets);
  assert.deepEqual(source.data.http, automatic.data.http, 'chart grouping leaves every total and endpoint unchanged');
  assert.deepEqual(source.data.events, automatic.data.events);
  assert.equal(source.data.events.length, 48);
  assert.equal(source.data.http.routes.length, 10, 'all instrumented templates, including idle unmatched, remain available');
  assert.ok(!source.data.history_truncated);
  assert.equal(source.data.charts.reduce((sum, point) => sum + point.requests, 0), expected.requests);
  const histogram = source.data.charts.reduce((sum, point) => sum.map((n, i) => n + point.duration_buckets[i]), Array(14).fill(0));
  assert.deepEqual(histogram, expected.buckets);
  // Hide all endpoint/event rows in the UI before exporting. The JSON remains
  // the complete successful query, with no pagination or search applied to it.
  await page.locator('#route-search').fill('no such fictional endpoint');
  await page.locator('#event-search').fill('no such fictional event');
  await page.locator('#export-view').click();
  const exported = await page.evaluate(async () => JSON.parse(await window.__fixtureExport.text()));
  assert.equal(exported.format, 'bobowatch.overview.v1');
  assert.deepEqual(exported.overview.http, source.data.http);
  assert.deepEqual(exported.overview.charts, source.data.charts);
  assert.deepEqual(exported.overview.events, source.data.events);
  assert.ok(Object.values(exported.scope).every(value => !value), 'export scope remains the unfiltered query');
  assert.ok(!JSON.stringify(exported).includes('token_hash'));
  await page.locator('#route-search').fill('');
  await page.locator('#event-search').fill('');
  await update(page, () => page.locator('#chart-detail').selectOption('auto'));
  await update(page, () => page.locator('#window').selectOption('300', {force: true}));
}
async function checkWorkspace(page) {
  await update(page, () => page.locator('#project').selectOption('travel'));
  await update(page, () => page.locator('#site').selectOption('flights'));
  await page.locator('#route-search').fill('/api/search');
  await page.locator('#route-sort').selectOption('errors');
  await page.locator('#route-minimum').selectOption('1');
  await page.locator('#route-page-size').selectOption('all');
  await page.locator('#event-search').fill('Fictional');
  await page.locator('#event-severity').selectOption('warning');
  await page.locator('#heatmap-mode').selectOption('share');
  await page.locator('#error-view').selectOption('count');
  await page.locator('#copy-view').click();
  await page.waitForFunction(() => document.getElementById('view-link').value.includes('start_ms='));
  const link = await page.locator('#view-link').inputValue();
  const parsed = new URL(link);
  assert.equal(parsed.searchParams.get('v'), '1');
  assert.equal(parsed.searchParams.get('window'), 'custom');
  assert.equal(parsed.searchParams.get('site'), 'flights');
  assert.equal(parsed.searchParams.get('routeQuery'), '/api/search');
  assert.equal(parsed.searchParams.get('heatmap'), 'share');
  assert.equal(parsed.searchParams.get('errors'), 'count');
  assert.equal(parsed.searchParams.get('refresh'), '0');
  assert.ok(!/token|secret|authorization/i.test(parsed.search));
  await page.goto(link);
  await loaded(page);
  assert.equal(await page.locator('#project').inputValue(), 'travel');
  assert.equal(await page.locator('#site').inputValue(), 'flights');
  assert.equal(await page.locator('#route-search').inputValue(), '/api/search');
  assert.equal(await page.locator('#route-sort').inputValue(), 'errors');
  assert.equal(await page.locator('#event-search').inputValue(), 'Fictional');
  assert.equal(await page.locator('#event-severity').inputValue(), 'warning');
  assert.match(await page.locator('#scope-label').textContent(), /Flight site/);
  const first = await update(page, () => page.reload());
  assert.equal(first.query.get('start_ms'), parsed.searchParams.get('start_ms'));
  assert.equal(first.query.get('end_ms'), parsed.searchParams.get('end_ms'));
  const previous = await update(page, () => page.locator('#previous-range').click());
  assert.equal(Number(previous.query.get('end_ms')), Number(parsed.searchParams.get('start_ms')));
  assert.equal(Number(previous.query.get('end_ms')) - Number(previous.query.get('start_ms')),
    Number(parsed.searchParams.get('end_ms')) - Number(parsed.searchParams.get('start_ms')));
  const next = await update(page, () => page.locator('#next-range').click());
  assert.equal(next.query.get('start_ms'), parsed.searchParams.get('start_ms'));
  assert.equal(next.query.get('end_ms'), parsed.searchParams.get('end_ms'));
  await update(page, () => page.locator('#window').selectOption('600', {force: true}));
  await update(page, () => page.locator('#window').selectOption('900', {force: true}));
  let result = await update(page, () => page.goBack());
  assert.equal(result.query.get('window'), '600');
  result = await update(page, () => page.goForward());
  assert.equal(result.query.get('window'), '900');
  await page.locator('#event-search').fill('');
  await page.locator('#event-severity').selectOption('');
  const incident = page.locator('[data-event-at]').first();
  const at = Number(await incident.getAttribute('data-event-at'));
  result = await update(page, () => incident.click());
  assert.equal(result.query.get('start_ms'), String(Math.max(0, at - 300000)));
  assert.ok(Number(result.query.get('end_ms')) > at);
  assert.ok(Number(result.query.get('end_ms')) <= at + 300000);
}
async function checkInvalidLink(context) {
  const page = await context.newPage();
  let overviews = 0;
  page.on('request', request => { if (request.url().includes('/api/overview?')) overviews++; });
  try {
    await page.goto(input.url + '?v=1&project=travel&site=bad%2Fidentifier&token=never-query-this');
    await page.locator('#reset-view').waitFor({state: 'visible'});
    assert.equal(overviews, 0, 'invalid links must not silently broaden or alter scope');
    assert.match(await page.locator('#workspace-message').textContent(), /No query/);
    await update(page, () => page.locator('#reset-view').click());
    assert.equal(overviews, 1);
    assert.ok(!page.url().includes('never-query-this'));
  } finally { await page.close(); }
}
async function checkMissingScope(context) {
  const page = await context.newPage();
  try {
    await page.goto(input.url + '?v=1&site=unregistered-site&window=300&refresh=0');
    await loaded(page);
    assert.equal(await page.locator('#site').inputValue(), 'unregistered-site');
    assert.match(await page.locator('#site option:checked').textContent(), /not in inventory/);
    assert.match(await page.locator('#sites').textContent(), /No sites/);
    const result = await update(page, () => page.reload());
    assert.equal(result.query.get('site'), 'unregistered-site');
    assert.equal(result.data.sites.length, 0);
  } finally { await page.close(); }
}
async function checkOffline(context, page) {
  await context.setOffline(true);
  try {
    await page.waitForFunction(() => document.getElementById('connection').textContent === 'Browser offline');
    assert.match(await page.locator('#notice').textContent(), /Browser offline/);
    assert.match(await page.locator('#summary > div').first().locator('strong').textContent(), /^—(?:req\/s)?$/);
  } finally {
    await update(page, () => context.setOffline(false));
  }
  assert.equal(await page.locator('#connection').textContent(), 'Last query succeeded');
}
async function checkQueryInteraction(page) {
  const shownRange = await page.locator('#range-status').textContent();
  let release, intercepted;
  const held = new Promise(resolve => { release = resolve; });
  const started = new Promise(resolve => { intercepted = resolve; });
  const delay = async route => {
    const query = new URL(route.request().url()).searchParams;
    if (query.get('window') !== '1800') return route.continue();
    intercepted();
    await held;
    // The browser may have aborted a superseded request while its server query
    // continues. A transport abort is expected; no server cancellation is claimed.
    try { await route.continue(); } catch {}
  };
  await page.route('**/api/overview?**', delay);
  try {
    await page.locator('#window').selectOption('1800', {force: true});
    await started;
    assert.equal(await page.locator('#range-status').textContent(), shownRange);
    await page.locator('#cancel-query').click();
    await page.waitForFunction(() => !document.getElementById('refresh').disabled);
    assert.match(await page.locator('#notice').textContent(), /cancelled/);
    assert.equal(await page.locator('#range-status').textContent(), shownRange);
    // A new range starts without waiting for the held cancelled transport.
    const latest = await update(page, () => page.locator('#window').selectOption('600', {force: true}));
    assert.equal(latest.query.get('window'), '600');
    const latestRange = await page.locator('#range-status').textContent();
    release();
    await page.waitForTimeout(100);
    assert.equal(await page.locator('#range-status').textContent(), latestRange);
  } finally { release(); await page.unroute('**/api/overview?**', delay); }
  // A directly superseded outstanding request also cannot overwrite its successor.
  let releaseAgain, interceptedAgain;
  const heldAgain = new Promise(resolve => { releaseAgain = resolve; });
  const startedAgain = new Promise(resolve => { interceptedAgain = resolve; });
  const delayedAgain = async route => {
    if (new URL(route.request().url()).searchParams.get('window') !== '1800') return route.continue();
    interceptedAgain(); await heldAgain;
    try { await route.continue(); } catch {}
  };
  await page.route('**/api/overview?**', delayedAgain);
  try {
    await page.locator('#window').selectOption('1800', {force: true}); await startedAgain;
    await update(page, () => page.locator('#window').selectOption('900', {force: true}));
    releaseAgain();
    await page.waitForTimeout(100);
    assert.equal(await page.locator('#window').inputValue(), '900');
    assert.match(page.url(), /window=900/);
  } finally { releaseAgain(); await page.unroute('**/api/overview?**', delayedAgain); }
}
async function checkFreshness(page) {
  const fixture = async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.stale_seconds = 1;
    data.current = {rps: 7.5, in_flight: 2};
    for (const site of data.sites) for (const replica of site.replicas) {
      replica.observed_at_ms = replica.received_at_ms = data.now_ms;
      replica.status = replica.retired ? 'retired' : 'fresh';
    }
    await route.fulfill({response, json: data});
  };
  await page.route('**/api/overview?**', fixture);
  try {
    await update(page, () => page.locator('#window').selectOption('300', {force: true}));
    assert.match(await page.locator('#telemetry-status').textContent(), /active replicas reporting/);
    assert.equal(await page.locator('#summary > div').first().locator('strong small').textContent(), 'req/s');
    assert.equal(await page.locator('#summary > div').nth(3).locator('strong small').textContent(), 'active');
    await page.waitForFunction(() => /0\//.test(document.getElementById('telemetry-status').textContent), {timeout: 10000});
    assert.match(await page.locator('#summary > div').first().locator('strong').textContent(), /^—(?:req\/s)?$/);
    assert.equal(await page.locator('#summary > div').first().locator('strong small').textContent(), 'req/s');
    assert.equal(await page.locator('#summary > div').nth(3).locator('strong small').textContent(), 'active');
    assert.match(await page.locator('#notice').textContent(), /no current telemetry/);
    assert.match(await page.locator('#refresh-status').textContent(), /Auto-refresh off/);
  } finally { await page.unroute('**/api/overview?**', fixture); }
}
async function checkQuotaAndEpoch(page) {
  const changedEpoch = 'e2317d91-98c0-4a2e-b4d1-21cc7b06e753';
  const fixture = async route => {
    const response = await route.fetch(), data = await response.json();
    Object.assign(data.storage, {available: true, ram_only: true, epoch: changedEpoch,
      quota_bytes: 5 * 2 ** 30, used_bytes: 4.75 * 2 ** 30, reserve_bytes: .5 * 2 ** 30,
      quota_applied: true, admission_available: false, main_bytes: 4 * 2 ** 30,
      wal_bytes: .7 * 2 ** 30, shm_bytes: .05 * 2 ** 30, evicted_snapshots: 123456,
      volume_bytes: 5 * 2 ** 30, volume_free_bytes: .25 * 2 ** 30});
    data.configuration.status = 'rejected';
    data.configuration.error_type = 'Invalid';
    data.ready = false;
    await route.fulfill({response, json: data});
  };
  await page.route('**/api/operations', fixture);
  try {
    await page.locator('#refresh-operations').click();
    await page.waitForFunction(() => /Configuration reload was rejected/.test(document.getElementById('operations-message').textContent));
    assert.match(await page.locator('#operations-message').textContent(), /previous valid settings remain active/);
    assert.match(await page.locator('#operations-summary').textContent(), /Unavailable/);
    assert.equal(await page.locator('#storage-meter').evaluate(node => node.value), .95);
    assert.match(await page.locator('#storage-meter').getAttribute('aria-valuetext'), /4.75 GiB used of 5 GiB/);
    assert.match(await page.locator('#storage-capacity').textContent(), /95%.*0 B headroom/);
    assert.equal(await page.locator('#storage-capacity').getAttribute('class'), 'error-number');
    assert.match(await page.locator('#operations-details').textContent(), /rejected \(Invalid\)/);
    assert.match(await page.locator('#operations-details').textContent(), /RAM only/);
    assert.match(await page.locator('#operations-summary').textContent(), /123,456 old observations evicted/);
    await page.waitForFunction(() => /epoch changed/.test(document.getElementById('notice').textContent));
  } finally { await page.unroute('**/api/operations', fixture); }
}
async function checkConnectionRetry(context) {
  const page = await context.newPage();
  const fail = route => route.fulfill({status: 503, body: '{}', contentType: 'application/json'});
  await page.route('**/api/access', fail);
  try {
    await page.goto(input.url);
    await page.locator('#retry-connect').waitFor({state: 'visible'});
    await page.unroute('**/api/access', fail);
    await page.locator('#retry-connect').click();
    await loaded(page);
  } finally { await page.close(); }
}
async function main() {
  for (const [name, engine, phone] of [['chromium', chromium, false], ['webkit', webkit, true]]) {
    console.log(`Browser checks: ${name} ${phone ? 'phone' : 'desktop'} · fictional runtime`);
    const browser = await engine.launch({headless: true});
    const context = await browser.newContext({timezoneId: 'America/Toronto', reducedMotion: 'reduce',
      viewport: phone ? {width: 390, height: 844} : {width: 1440, height: 1000},
      ...(phone ? {isMobile: true, hasTouch: true} : {})});
    await context.addInitScript(() => {
      // Exercise the portable copy fallback without requiring clipboard permission.
      const create = URL.createObjectURL.bind(URL);
      URL.createObjectURL = blob => { window.__fixtureExport = blob; return create(blob); };
      const click = HTMLAnchorElement.prototype.click;
      HTMLAnchorElement.prototype.click = function () {
        if (this.download && this.href.startsWith('blob:')) return;
        return click.call(this);
      };
      Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async () => { throw new Error('Fixture clipboard unavailable'); }}});
    });
    const page = await context.newPage(), errors = [];
    page.setDefaultTimeout(15000);
    page.on('pageerror', error => errors.push(error.message));
    let phase = 'default render';
    try {
      await page.goto(input.url); await loaded(page);
      assert.equal(await page.locator('#window').inputValue(), '300');
      assert.equal(await page.locator('#auto-refresh').inputValue(), '300');
      await page.locator('#auto-refresh').selectOption('0');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'default dashboard has no viewport overflow');
      assert.ok(await page.locator('#duration-heatmap svg').count());
      await preview(page, `${name}-${phone ? 'phone' : 'desktop'}-overview`);
      phase = 'chart keyboard/touch inspection'; await runCharts({page, phone});
      phase = 'operations failure and recovery'; await checkOperations(page);
      await page.locator('#operations-section').scrollIntoViewIfNeeded();
      await preview(page, `${name}-${phone ? 'phone' : 'desktop'}-operations`);
      phase = 'complete all-history, source detail and unfiltered export'; await checkCompleteHistory(page);
      phase = 'workspace links, reload, periods, history and event focus'; await checkWorkspace(page);
      phase = 'query cancellation and newest request'; await checkQueryInteraction(page);
      phase = 'freshness expires without another poll'; await checkFreshness(page);
      phase = 'offline and online recovery'; await checkOffline(context, page);
      phase = 'unknown scope remains explicit'; await checkMissingScope(context);
      phase = 'invalid link and explicit reset'; await checkInvalidLink(context);
      phase = 'initial connection retry'; await checkConnectionRetry(context);
      phase = 'RAM quota, config rejection and changed epoch'; await checkQuotaAndEpoch(page);
      await page.locator('#operations-section').scrollIntoViewIfNeeded();
      await preview(page, `${name}-${phone ? 'phone' : 'desktop'}-capacity`);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'dashboard has no viewport overflow after investigation');
      assert.deepEqual(errors, []);
      console.log(`${name} ${phone ? 'phone' : 'desktop'}: all dashboard checks passed; zero script errors`);
    } catch (error) {
      error.message = `${name} ${phone ? 'phone' : 'desktop'} / ${phase}: ${error.message}`;
      throw error;
    } finally { await context.close(); await browser.close(); }
  }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
