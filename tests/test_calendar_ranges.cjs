const assert = require('node:assert/strict');
const test = require('node:test');
process.env.TZ = 'America/Toronto';
const ranges = require('../bobowatch/web/ranges.js');
const at = value => new Date(value).getTime();
const now = at('2026-10-24T13:30:45-04:00');

function bounds(preset, start, end, stamp=now) {
  const range = ranges.resolve(preset, stamp);
  assert.equal(range.start_ms, at(start));
  assert.equal(range.end_ms, end ? at(end) : stamp);
  assert.equal(range.absolute, true);
  return range;
}

test('rolling ranges are exact elapsed durations, including across DST', () => {
  for (const preset of ranges.rolling) {
    const range = ranges.resolve(preset, now);
    assert.equal(range.end_ms - range.start_ms, Number(preset) * 1000);
    assert.equal(range.absolute, false);
    assert.equal(range.window, Number(preset));
  }
  const range = ranges.resolve('604800', at('2026-03-09T12:00:00-04:00'));
  assert.equal(range.end_ms - range.start_ms, 168 * 3600000);
});

test('calendar ranges use local boundaries and Monday weeks', () => {
  bounds('today', '2026-10-24T00:00:00-04:00');
  bounds('yesterday', '2026-10-23T00:00:00-04:00', '2026-10-24T00:00:00-04:00');
  bounds('this-week', '2026-10-19T00:00:00-04:00');
  bounds('last-week', '2026-10-12T00:00:00-04:00', '2026-10-19T00:00:00-04:00');
  bounds('this-month', '2026-10-01T00:00:00-04:00');
  bounds('last-month', '2026-09-01T00:00:00-04:00', '2026-10-01T00:00:00-04:00');
  bounds('this-year', '2026-01-01T00:00:00-05:00');
  bounds('last-year', '2025-01-01T00:00:00-05:00', '2026-01-01T00:00:00-05:00');
  bounds('this-week', '2026-10-19T00:00:00-04:00', null, at('2026-10-25T12:00:00-04:00'));
  bounds('this-week', '2026-10-26T00:00:00-04:00', null, at('2026-10-26T12:00:00-04:00'));
});

test('calendar days and weeks handle the 23/25-hour daylight-saving days', () => {
  const spring = bounds('yesterday', '2026-03-08T00:00:00-05:00', '2026-03-09T00:00:00-04:00', at('2026-03-09T12:00:00-04:00'));
  assert.equal(spring.end_ms - spring.start_ms, 23 * 3600000);
  const autumn = bounds('yesterday', '2026-11-01T00:00:00-04:00', '2026-11-02T00:00:00-05:00', at('2026-11-02T12:00:00-05:00'));
  assert.equal(autumn.end_ms - autumn.start_ms, 25 * 3600000);
  const week = bounds('last-week', '2026-03-02T00:00:00-05:00', '2026-03-09T00:00:00-04:00', at('2026-03-09T12:00:00-04:00'));
  assert.equal(week.end_ms - week.start_ms, 167 * 3600000);
});

test('month and year rollover uses civil dates, including leap February', () => {
  bounds('last-month', '2025-12-01T00:00:00-05:00', '2026-01-01T00:00:00-05:00', at('2026-01-15T12:00:00-05:00'));
  const leap = bounds('last-month', '2024-02-01T00:00:00-05:00', '2024-03-01T00:00:00-05:00', at('2024-03-15T12:00:00-04:00'));
  assert.equal(leap.end_ms - leap.start_ms, 29 * 86400000);
  const year = bounds('last-year', '2024-01-01T00:00:00-05:00', '2025-01-01T00:00:00-05:00', at('2025-01-15T12:00:00-05:00'));
  assert.equal(year.end_ms - year.start_ms, 366 * 86400000);
});

test('moving calendar periods advance while applied custom ranges stay fixed', () => {
  const custom = {start_ms: now - 60000, end_ms: now};
  assert.deepEqual(ranges.resolve('custom', now + 600000, custom), {...custom, absolute:true});
  assert.equal(ranges.resolve('today', now + 600000).end_ms, now + 600000);
  assert.equal(ranges.resolve('today', at('2026-10-25T00:00:00-04:00')).end_ms, at('2026-10-25T00:00:00-04:00')+1);
});

test('native calendar values round-trip and reject invalid/nonexistent times', () => {
  const value = '2026-10-24T13:30:45';
  assert.equal(ranges.toLocalInput(now), value);
  assert.equal(ranges.parseLocalInput(value), now);
  assert.equal(ranges.toLocalInput(at('2026-01-01T00:00:00-05:00')), '2026-01-01T00:00');
  for (const value of ['', '2026-02-31T12:00', '2026-03-08T02:30', '1969-12-31T00:00', '2026-10-24T24:00'])
    assert.throws(()=>ranges.parseLocalInput(value), RangeError);
  assert.equal(ranges.parseLocalInput('2026-11-01T01:30'), at('2026-11-01T01:30:00-04:00'));
});

test('custom ranges reject reversed, empty, non-integer and future bounds', () => {
  for (const custom of [null, {}, {start_ms:now,end_ms:now}, {start_ms:now,end_ms:now-1},
    {start_ms:-1,end_ms:now}, {start_ms:'1',end_ms:now}, {start_ms:1.5,end_ms:now},
    {start_ms:0,end_ms:now+30001}]) assert.throws(()=>ranges.resolve('custom',now,custom), RangeError);
  assert.throws(()=>ranges.resolve('unknown',now),RangeError);
});
