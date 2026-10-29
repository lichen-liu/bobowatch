const assert = require('node:assert/strict');
const test = require('node:test');
const {endpoints, endpointContributions, latencyHeatmap, compareSites} = require('../bobowatch/web/metrics.js');
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

test('endpoint contribution denominators include the entire selected scope, before search or paging', () => {
  const before = structuredClone(rows);
  const total = endpointContributions(rows);
  assert.deepEqual(total, {requests:1011, errors:11, duration_us:30010000});
  const filtered = endpoints(rows, {query:'/quiet',minimum:1,size:1}).rows[0];
  assert.equal(filtered.requests / total.requests, 1 / 1011);
  assert.equal(filtered.errors / total.errors, 1 / 11);
  assert.equal(filtered.duration_us / total.duration_us, 10000 / 30010000);
  assert.deepEqual(endpointContributions(rows), total, 'table filters do not change the denominators');
  assert.deepEqual(rows, before);
  assert.deepEqual(endpointContributions([rows[3]]), {requests:0,errors:0,duration_us:0});
  assert.deepEqual(endpointContributions([]), {requests:0,errors:0,duration_us:0});
});

function point(at_ms, bucket, requests, quality = {}, partial = false) {
  const duration_buckets = Array(14).fill(0);
  duration_buckets[bucket] = requests;
  return {at_ms, duration_buckets, requests, quality:{snapshots:1,partial:0,skewed:0,extended_http:1,...quality},partial};
}

test('latency heatmap distinguishes missing time columns from observed zero request counts', () => {
  const points = [
    point(100, 0, 10, {snapshots:2,partial:1,skewed:1,extended_http:2}, true),
    point(150, 2, 5),
    point(300, 0, 0),
    point(400, 1, 2, {}, true),
    point(1000, 13, 3),
    point(-1, 0, 999),
    point(1001, 0, 999),
  ];
  const before = structuredClone(points);
  const cells = latencyHeatmap(points, {start:0,end:1000,columns:10});
  assert.equal(cells.length, 10);
  assert.equal(cells[0].observed, false);
  assert.equal(cells[0].requests, 0);
  assert.equal(cells[0].source_points, 0);
  assert.equal(cells[3].observed, true, 'zero traffic still has an actual observation');
  assert.equal(cells[3].requests, 0);
  assert.equal(cells[3].quality.snapshots, 1);
  assert.equal(cells[1].at_ms, 100);
  assert.equal(cells[1].start_ms, 100);
  assert.equal(cells[1].end_ms, 200);
  assert.equal(cells[1].requests, 15);
  assert.equal(cells[1].buckets[0], 10);
  assert.equal(cells[1].buckets[2], 5);
  assert.equal(cells[1].source_points, 2);
  assert.deepEqual(cells[1].quality, {snapshots:3,partial:1,skewed:1,extended_http:3});
  assert.equal(cells[1].partial, true);
  assert.equal(cells[4].partial, true, 'missing expected replicas can make a point partial without a partial-source flag');
  assert.equal(cells[4].quality.partial, 0);
  assert.equal(cells[9].buckets[13], 3, 'an inclusive closing timestamp remains in the last column');
  assert.equal(cells.reduce((sum, column) => sum + column.requests, 0), 20);
  assert.deepEqual(points, before, 'aggregation does not change source arrays or quality counters');
});

test('heatmap columns align source milliseconds and clip exact bounds when they divide unevenly', () => {
  const cells = latencyHeatmap([1000,1002,1003,1005,1006,1009,1010].map(at=>point(at,0,at-1000+1)),
    {start:1000,end:1010,columns:3});
  assert.deepEqual(cells.map(column=>[column.start_ms,column.end_ms]), [[1000,1004],[1004,1008],[1008,1010]]);
  assert.deepEqual(cells.map(column=>column.requests), [8,13,21]);
  assert.equal(cells[0].end_ms, cells[1].start_ms);
  assert.equal(cells[1].end_ms, cells[2].start_ms);
  const narrow = latencyHeatmap([point(0,0,1),point(1,0,2)],{start:0,end:1,columns:100});
  assert.equal(narrow.length, 1, 'display columns are not narrower than a source timestamp');
  assert.equal(narrow[0].requests, 3);
  assert.deepEqual(latencyHeatmap([],{start:0,end:10,columns:2}).map(cell=>cell.observed), [false,false]);
});

test('coarse daily API groups remain whole without invented empty time cells', () => {
  const day = 86400000, start = 2.5 * day, end = 5.25 * day;
  const points = [point(start,0,10),point(3*day,1,20),point(4*day,2,30),point(5*day,3,0)];
  const cells = latencyHeatmap(points,{start,end,columns:100,bucket_ms:day});
  assert.equal(cells.length, 4, 'one daily group does not become dozens of falsely missing columns');
  assert.deepEqual(cells.map(cell=>[cell.start_ms,cell.end_ms]),
    [[start,3*day],[3*day,4*day],[4*day,5*day],[5*day,end]]);
  assert.deepEqual(cells.map(cell=>cell.observed), [true,true,true,true]);
  assert.deepEqual(cells.map(cell=>cell.requests), [10,20,30,0]);
  assert.equal(cells.reduce((sum,cell)=>sum+cell.source_points,0), points.length);
  assert.equal(cells.reduce((sum,cell)=>sum+cell.requests,0), 60);
});

test('display groups combine whole API buckets across offset range boundaries', () => {
  const day = 86400000, start = 2.25 * day, end = 9.75 * day;
  const points = Array.from({length:8},(_,i)=>point(i ? (i+2)*day : start,i, i+1));
  const cells = latencyHeatmap(points,{start,end,columns:3,bucket_ms:day});
  assert.equal(cells.length, 4, 'an offset edge can add one column to the visual target');
  assert.deepEqual(cells.map(cell=>[cell.start_ms,cell.end_ms]),
    [[start,3*day],[3*day,6*day],[6*day,9*day],[9*day,end]]);
  assert.deepEqual(cells.map(cell=>cell.requests), [1,9,18,8]);
  for (const source of points) {
    const representedStart = Math.max(start,Math.floor(source.at_ms/day)*day);
    const representedEnd = Math.min(end,Math.floor(source.at_ms/day)*day+day);
    const column = cells.find(cell=>cell.start_ms<=source.at_ms&&source.at_ms<cell.end_ms);
    assert.ok(column.start_ms <= representedStart && column.end_ms >= representedEnd,
      'the API group interval remains entirely in one displayed column');
  }
  const closing = latencyHeatmap([...points,point(end,13,5)],{start,end,columns:3,bucket_ms:day});
  assert.equal(closing.at(-1).buckets[13], 5);
  assert.equal(closing.reduce((sum,cell)=>sum+cell.requests,0), 41);
  for (const columns of [1,2,3,7,33,1000]) {
    const grouped = latencyHeatmap(points,{start,end,columns,bucket_ms:day});
    assert.ok(grouped.length <= columns+1);
    assert.equal(grouped.reduce((sum,cell)=>sum+cell.requests,0), 36);
  }
});

test('latency display grouping conserves every bin, source count and quality flag across over 200,000 points', () => {
  const length = 210005;
  const points = Array.from({length}, (_,i)=>point(i*10,i%14,i%5+1,{
    snapshots:i%3+1,
    partial:i%17===0 ? 1 : 0,
    skewed:i%101===0 ? 1 : 0,
    extended_http:i%4===0 ? 0 : 1,
  },i%97===0));
  const cells = latencyHeatmap(points,{start:0,end:(length-1)*10,columns:127});
  const bins = Array(14).fill(0), actualBins = Array(14).fill(0);
  const quality = {snapshots:0,partial:0,skewed:0,extended_http:0};
  const actualQuality = {...quality};
  for (const source of points) {
    for (let index=0;index<14;index++) bins[index] += source.duration_buckets[index];
    for (const key of Object.keys(quality)) quality[key] += source.quality[key];
  }
  for (const cell of cells) {
    for (let index=0;index<14;index++) actualBins[index] += cell.buckets[index];
    for (const key of Object.keys(actualQuality)) actualQuality[key] += cell.quality[key];
    assert.equal(cell.requests, cell.buckets.reduce((sum,count)=>sum+count,0));
    assert.equal(cell.observed, true);
    assert.equal(cell.partial, true);
    assert.ok(Math.abs(cell.buckets.reduce((sum,count)=>sum+count/cell.requests,0) - 1) < 1e-12);
  }
  assert.deepEqual(actualBins, bins);
  assert.deepEqual(actualQuality, quality);
  assert.equal(cells.reduce((sum,column)=>sum+column.source_points,0), length);
  assert.equal(cells.reduce((sum,column)=>sum+column.requests,0), bins.reduce((sum,count)=>sum+count,0));
});

test('invalid heatmap geometry fails explicitly instead of silently losing observations', () => {
  for (const range of [
    {start:0,end:0,columns:1}, {start:10,end:0,columns:1},
    {start:0,end:10,columns:0}, {start:0,end:10,columns:1.5},
    {start:0,end:Infinity,columns:1}, {start:0,end:10,columns:NaN},
    {start:0,end:10,columns:1,bucket_ms:0}, {start:0,end:10,columns:1,bucket_ms:1.5},
  ]) assert.throws(()=>latencyHeatmap([],{...range}), RangeError);
  assert.throws(()=>latencyHeatmap([{at_ms:1,duration_buckets:[1]}],{start:0,end:10,columns:2}), TypeError);
});

test('site comparison ranks observed problems, preserves unknown values and uses stable site IDs for ties', () => {
  const sites = [
    {id:'busy',http:{requests:1000,errors:10,mean_ms:10,latency:{p95:{upper_ms:25,overflow:false}},slow_request_pct:1}},
    {id:'slow',http:{requests:10,errors:0,mean_ms:2000,latency:{p95:{upper_ms:2500,overflow:false}},slow_request_pct:90}},
    {id:'failing',http:{requests:1,errors:1,mean_ms:10,latency:{p95:{upper_ms:null,overflow:true}},slow_request_pct:100}},
    {id:'idle',http:{requests:0,errors:0,mean_ms:null,latency:{p95:null},slow_request_pct:null}},
    {id:'zero-mean',http:{requests:1,errors:0,mean_ms:0,latency:{p95:{upper_ms:5,overflow:false}},slow_request_pct:0}},
  ];
  const before = structuredClone(sites);
  for (const [sort,id] of [['requests','busy'],['errors','busy'],['error-rate','failing'],
    ['mean','slow'],['p95','failing'],['slow','failing']]) {
    const ranked = compareSites(sites,sort);
    assert.equal(ranked[0].id, id);
    assert.equal(ranked.at(-1).id, 'idle', `${sort}: an idle site does not imply measured zero latency or rate`);
  }
  assert.equal(compareSites(sites,'invalid')[0].id, 'busy');
  assert.deepEqual(sites, before);
  const ties = [{id:'z',http:{requests:1,errors:0,mean_ms:null}},{id:'a',http:{requests:1,errors:0,mean_ms:null}}];
  assert.deepEqual(compareSites(ties,'mean').map(site=>site.id), ['a','z']);
  assert.deepEqual(compareSites([...ties].reverse(),'mean').map(site=>site.id), ['a','z']);
  assert.deepEqual(compareSites([], 'requests'), []);
});
