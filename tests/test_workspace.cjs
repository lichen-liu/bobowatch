const assert = require('node:assert/strict');
const test = require('node:test');
const workspace = require('../bobowatch/web/workspace.js');
const now = Date.parse('2026-10-29T13:35:42.123Z');
const decode = search => workspace.decode(search, {now});
const allScope = {project: '', site: '', host: '', replica: ''};
const view = {
  project: 'my-project', site: 'api.example', host: 'Mac_1', replica: 'worker-2',
  window: '3600', refresh: '60', detail: 'auto', traffic: 'rate', errors: 'count',
  heatmap: 'share', serviceSort: 'p95', routeQuery: '/api/検索 🚀 & size=2',
  routeSort: 'work', routeMinimum: '10', routeSize: 'all', eventQuery: 'Deployment ✓',
  eventSeverity: 'warning', eventSize: '100',
};

test('empty URL leaves browser preferences alone; explicit link clears omitted scopes', () => {
  assert.deepEqual(decode(''), {present: false, valid: true, state: {}, warnings: []});
  assert.deepEqual(decode('?'), {present: false, valid: true, state: {}, warnings: []});
  const preferences = {project: 'old-project', site: 'old-site', window: '300', refresh: '300', detail: 'auto'};
  assert.deepEqual(workspace.merge(preferences, decode('')), preferences);
  assert.deepEqual(workspace.merge(preferences, decode('?v=1&window=3600&refresh=0')),
    {...preferences, ...allScope, window: '3600', refresh: '0'});
});

test('allowlisted workspace round-trips Unicode filters and exact scope IDs', () => {
  const query = workspace.encode(view, {now});
  assert.equal(new URLSearchParams(query).get('v'), '1');
  assert.deepEqual(decode(query), {present: true, valid: true, state: view, warnings: []});
  assert.deepEqual(view.routeQuery, '/api/検索 🚀 & size=2');
});

test('share links freeze the successful displayed bounds instead of moving the range', () => {
  const fixedRange = {range_start_ms: now - 3600000, range_end_ms: now - 30000};
  const query = workspace.encode({...view, custom: {start_ms: 10, end_ms: 20}}, {fixedRange, now});
  const state = decode(query).state;
  assert.equal(state.window, 'custom');
  assert.deepEqual(state.custom, {start_ms: fixedRange.range_start_ms, end_ms: fixedRange.range_end_ms});
  const later = workspace.decode(query, {now: now + 86400000});
  assert.deepEqual(later.state, state);
  assert.equal(view.window, '3600');
});

test('encoding strips secret and unknown properties without adding credential values', () => {
  const query = workspace.encode({...view, token: 'secret-ADMIN', authorization: 'Bearer secret-ADMIN',
    token_file: '/Users/private/secret', server: 'unexpected', access: 'local'}, {now});
  assert.ok(!query.includes('secret'));
  assert.ok(!query.includes('token'));
  assert.deepEqual(decode(query).state, view);
  const bad = decode('?project=my-project&token=secret-ADMIN');
  assert.equal(bad.valid, false);
  assert.equal(bad.state, null);
  assert.ok(!JSON.stringify(bad).includes('secret-ADMIN'));
});

test('empty text and severity filters are encoded explicitly to override saved filters', () => {
  const empty = {routeQuery: '', eventQuery: '', eventSeverity: ''};
  const query = workspace.encode(empty, {now});
  assert.deepEqual(decode(query).state, {...allScope, ...empty});
  const merged = workspace.merge({routeQuery: 'old', eventQuery: 'old', eventSeverity: 'error'}, decode(query));
  assert.deepEqual(merged, {...allScope, ...empty});
});

test('unknown, repeated and malformed parameters block the whole link rather than broaden scope', () => {
  for (const query of [
    '?project=good&site=not/a/site',
    '?project=one&project=two', '?window=300&window=3600', '?v=1&v=1', '?v=2',
    '?sitee=important-site', '?project=good&unknown=value', '?routeQuery=%E0%A4%A',
    '?%ZZ=1', '?site=%ZZ', '?project=../../secret', '?site=東京',
    '?host=bad+host', '?replica=' + 'x'.repeat(129), '?routeQuery=%00',
    '?routeQuery=' + 'x'.repeat(129), '?eventQuery=' + 'x'.repeat(257),
    '?eventQuery=%0Ahello',
  ]) {
    const result = decode(query);
    assert.equal(result.present, true, query);
    assert.equal(result.valid, false, query);
    assert.equal(result.state, null, query);
    assert.ok(result.warnings.length, query);
    assert.throws(() => workspace.merge({project: 'old'}, result), RangeError);
  }
  assert.deepEqual(decode('?project=good&site=').state, {...allScope, project: 'good'});
});

test('all enum values use the same strict choices as dashboard controls', () => {
  const allowed = {
    window: ['30', '31536000', 'today', 'last-year', 'all'],
    detail: ['auto', '1', '1000', '15000', '60000', '300000', '3600000', '86400000'],
    refresh: ['0', '30', '60', '300'], traffic: ['rate', 'count'], errors: ['rate', 'count'],
    heatmap: ['count', 'share'], serviceSort: ['requests', 'errors', 'error-rate', 'mean', 'p95', 'slow'],
    routeSort: ['requests', 'errors', 'error-rate', 'mean', 'work'],
    routeMinimum: ['0', '1', '10', '100', '1000'], routeSize: ['10', '25', '100', 'all'],
    eventSeverity: ['', 'info', 'warning', 'error'], eventSize: ['25', '100', 'all'],
  };
  for (const [key, values] of Object.entries(allowed)) {
    for (const value of values) assert.equal(decode(new URLSearchParams({[key]: value})).state[key], value);
    assert.equal(decode(new URLSearchParams({[key]: 'wrong'})).valid, false, key);
  }
  assert.equal(decode('?window=3601').valid, false); // UI URL preset choices; API rolling windows remain unrestricted.
});

test('custom boundaries preserve milliseconds, all retained duration, and future tolerance', () => {
  const state = decode(`?start_ms=0&end_ms=${now}`).state;
  assert.deepEqual(state, {...allScope, window: 'custom', custom: {start_ms: 0, end_ms: now}});
  assert.equal(decode(`?window=custom&start_ms=${now - 1}&end_ms=${now}`).valid, true);
  assert.equal(decode(`?window=custom&start_ms=1&end_ms=${now + 30000}`).valid, true);
  for (const query of [
    `?window=custom&start_ms=1&end_ms=${now + 30001}`,
    '?window=custom', '?start_ms=1', '?end_ms=2', '?start_ms=1&end_ms=1',
    '?start_ms=2&end_ms=1', '?start_ms=-1&end_ms=1', '?start_ms=1.5&end_ms=2',
    '?start_ms=0x10&end_ms=100', '?start_ms=1e2&end_ms=200',
    '?start_ms=1&end_ms=9007199254740992', '?window=300&start_ms=1&end_ms=2',
    '?window=all&start_ms=1&end_ms=2', '?start_ms=1&start_ms=1&end_ms=2',
  ]) assert.equal(decode(query).valid, false, query);
});

test('invalid known fields fail encoding instead of silently losing selected scope', () => {
  for (const invalid of [{project: 'bad/id'}, {detail: '999'}, {window: 'custom'},
    {eventQuery: 'line\nbreak'}, {routeQuery: '\uD800'}, {window: 'custom', custom: {start_ms: 20, end_ms: 10}}])
    assert.throws(() => workspace.encode(invalid, {now}), RangeError);
  assert.throws(() => workspace.encode(view, {now, fixedRange: {range_start_ms: 1, range_end_ms: now + 30001}}), RangeError);
});

test('previous and next periods preserve exact width without mutating range or timezone semantics', () => {
  const range = {range_start_ms: now - 7200000, range_end_ms: now - 3600000};
  assert.deepEqual(workspace.shiftRange(range, -1, now), {start_ms: now - 10800000, end_ms: now - 7200000});
  assert.deepEqual(workspace.shiftRange(range, 1, now), {start_ms: now - 3600000, end_ms: now});
  assert.deepEqual(range, {range_start_ms: now - 7200000, range_end_ms: now - 3600000});
  const dstDay = {range_start_ms: Date.parse('2026-03-08T00:00:00-05:00'), range_end_ms: Date.parse('2026-03-09T00:00:00-04:00')};
  const shifted = workspace.shiftRange(dstDay, -1, now);
  assert.equal(shifted.end_ms - shifted.start_ms, 23 * 3600000);
  for (const direction of [0, 2, '1', null]) assert.throws(() => workspace.shiftRange(range, direction, now), RangeError);
  assert.throws(() => workspace.shiftRange({range_start_ms: now - 1000, range_end_ms: now}, 1, now - 30000), RangeError);
  assert.throws(() => workspace.shiftRange({range_start_ms: 0, range_end_ms: 1000}, -1, now), RangeError);
});
