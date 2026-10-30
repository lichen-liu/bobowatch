const assert = require('node:assert/strict');
const test = require('node:test');
const {status, current, capacity} = require('../bobowatch/web/operations.js');
const fresh = {observed_at_ms:1000, received_at_ms:1200, status:'fresh'};
test('telemetry ages from both clocks, without mutating the queried observations', () => {
  assert.equal(status(fresh, 46000, 45), 'fresh');
  assert.equal(status(fresh, 46001, 45), 'stale');
  assert.equal(status({...fresh,observed_at_ms:50000}, 47000,45), 'stale');
  assert.equal(status({...fresh,received_at_ms:null}, 1200,45), 'waiting');
  assert.equal(status({...fresh,status:'skewed'}, 1200,45), 'skewed');
  assert.equal(status({...fresh,retired:true}, 47000,45), 'retired');
  assert.equal(fresh.status,'fresh');
});
test('current gauges expire while historical observations and retired replicas stay intact', () => {
  const gauges = {rps:2,in_flight:0};
  assert.deepEqual(current(gauges,[fresh],1200,45),gauges);
  for (const replicas of [[],[fresh,{...fresh,observed_at_ms:null}], [{...fresh,retired:true}]])
    assert.deepEqual(current(gauges,replicas,1200,45),{rps:null,in_flight:null});
  assert.deepEqual(current(gauges,[fresh],46001,45),{rps:null,in_flight:null});
  assert.deepEqual(current(gauges,[fresh],1200,45,true),{rps:null,in_flight:null});
  assert.deepEqual(current(gauges,[fresh,{retired:true}],1200,45),gauges);
});
test('capacity separates physical quota, reserve, missing data and enforcement', () => {
  assert.equal(capacity(null),null);
  assert.equal(capacity({available:false}),null);
  assert.deepEqual(capacity({available:true,used_bytes:0,quota_bytes:null}),
    {used:0,quota:null,ratio:null,headroom:null,warning:false});
  assert.deepEqual(capacity({used_bytes:95,quota_bytes:100,reserve_bytes:10,quota_applied:true}),
    {used:95,quota:100,ratio:.95,headroom:0,warning:true});
  assert.equal(capacity({used_bytes:20,quota_bytes:100,reserve_bytes:10,quota_applied:false}).warning,true);
});
