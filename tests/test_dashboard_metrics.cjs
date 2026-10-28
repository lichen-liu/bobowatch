const assert = require('node:assert/strict');
const test = require('node:test');
const {endpoints} = require('../bobowatch/web/metrics.js');
const rows = [
  {site:'alpha', route:'/busy', requests:1000, errors:10, duration_us:10000000},
  {site:'beta', route:'/slow', requests:10, errors:0, duration_us:20000000},
  {site:'alpha', route:'/quiet-failing', requests:1, errors:1, duration_us:10000},
  {site:'alpha', route:'/idle', requests:0, errors:0, duration_us:0},
];

test('endpoint rankings distinguish counts, rates, latency and total response time', () => {
  for (const [sort, route] of [['requests','/busy'], ['errors','/busy'],
    ['error-rate','/quiet-failing'], ['mean','/slow'], ['work','/slow']]) {
    assert.equal(endpoints(rows,{sort}).rows[0].route, route);
  }
  assert.equal(endpoints(rows,{sort:'bad'}).rows[0].route, '/busy');
  assert.equal(endpoints(rows).total, 4);
  assert.equal(rows[0].route, '/busy', 'sorting does not mutate source data');
});

test('all rows remain reachable, including idle routes and events beyond former cutoffs', () => {
  const many = Array.from({length:161},(_,i)=>({site:'test',route:`/${i}`,requests:i,errors:0,duration_us:i*100}));
  assert.equal(endpoints(many,{size:'all'}).rows.length,161);
  assert.equal(endpoints(many,{minimum:1,size:'all'}).total,160);
  const {events}=require('../bobowatch/web/metrics.js');
  const records=Array.from({length:550},(_,i)=>({site:'test',replica:'one',message:`event ${i}`,severity:i%2?'warning':'info'}));
  assert.equal(events(records,{size:'all'}).rows.length,550);
  assert.equal(events(records,{query:'event 549'}).total,1);
  assert.equal(events(records,{severity:'warning',size:'all'}).total,275);
  assert.equal(events(records,{size:100,page:5}).rows.length,50);
});

test('search spans site and route, while request minimum prevents low-sample rankings', () => {
  assert.deepEqual(endpoints(rows,{query:'  BETA '}).rows.map(r=>r.route), ['/slow']);
  assert.deepEqual(endpoints(rows,{query:'/QUIET-FAILING'}).rows.map(r=>r.route), ['/quiet-failing']);
  assert.equal(endpoints(rows,{minimum:10,sort:'error-rate'}).rows[0].route, '/busy');
  assert.equal(endpoints(rows,{query:'unmatched'}).total, 0);
});

test('pagination clamps after refresh or filtering and retains deterministic ties', () => {
  const many = Array.from({length:25},(_,i)=>({site:'test',route:`/${String(i).padStart(2,'0')}`,requests:1,errors:0,duration_us:100}));
  const last = endpoints(many.toReversed ? many.toReversed() : [...many].reverse(), {page:9});
  assert.equal(last.page,2);
  assert.equal(last.pages,3);
  assert.equal(last.rows.length,5);
  assert.equal(last.rows[0].route,'/20');
  const filtered = endpoints(many,{page:2,query:'/00'});
  assert.equal(filtered.page,0);
  assert.equal(filtered.pages,1);
  assert.equal(endpoints([],{page:1}).page,0);
});

test('inspection finds the nearest timestamp across large complete series', () => {
  const {nearestIndex}=require('../bobowatch/web/metrics.js');
  const points=Array.from({length:200000},(_,i)=>({at_ms:i*10}));
  assert.equal(nearestIndex(points,1999990),199999);
  assert.equal(nearestIndex(points,1234567),123457);
  assert.equal(nearestIndex(points,-1),0);
  assert.equal(nearestIndex(points,2000010),199999);
  assert.equal(nearestIndex([],0),-1);
});
