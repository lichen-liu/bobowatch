/* Development-only rendering checks; run with Playwright installed on NODE_PATH.
 * Uses fictional points in memory and the real lineChart function. No listener,
 * dashboard credentials, screenshots, downloads or production data are involved.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium, webkit} = require('playwright');
const root = path.resolve(__dirname, '../..');
const app = fs.readFileSync(path.join(root, 'bobowatch/web/app.js'), 'utf8');
const start = app.indexOf('function lineChart(');
const end = app.indexOf('\nfunction serviceComparison()', start);
assert.ok(start >= 0 && end > start, 'real chart renderer is present');
const chart = app.slice(start, end);
const support = `
const $ = id => document.getElementById(id);
const el = (tag, text, cls) => {const n = document.createElement(tag); if(text !== undefined)n.textContent=text; if(cls)n.className=cls; return n;};
const append = (n,...children) => {n.append(...children);return n;};
const shape = (svg,tag,attrs,text) => {const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);if(text!==undefined)n.textContent=text;svg.append(n);return n;};
const svgNode = (label,viewBox) => {const n=document.createElementNS('http://www.w3.org/2000/svg','svg');n.setAttribute('viewBox',viewBox);n.setAttribute('role','img');n.setAttribute('aria-label',label);return n;};
const rangeStamp = ms => new Date(ms).toISOString();
const axisClock = ms => String(ms);
const boundScale = n => Math.max(1,n);
const state = {hiddenSeries:new Map(),data:{range_start_ms:0,range_end_ms:10000,window_seconds:10,now_ms:10000,bucket_seconds:1,sample_seconds:1,charts:[
 {at_ms:1000,rps:0,requests:0,partial:false},
 {at_ms:2000,rps:2,requests:2,partial:true},
 {at_ms:9000,rps:null,requests:3,partial:false},
 {at_ms:10000,rps:10,requests:4,partial:false}
]}};
function render(){lineChart('traffic-chart','Traffic diagnostics',[
 {name:'Rate',color:'#123',get:p=>p.rps},
 {name:'Count',color:'#456',observed:true,get:p=>p.requests},
 {name:'Sparse',color:'#789',observed:true,points:state.data.charts.filter(p=>p.at_ms===1000||p.at_ms===10000),get:p=>p.requests}
],v=>String(v));}
`;

async function runIsolated() {
  const outcomes = [];
  for (const [name, engine] of Object.entries({chromium, webkit})) {
    const browser = await engine.launch({headless:true});
    try {
      for (const phone of [false,true]) {
        const context = await browser.newContext({viewport:{width:phone?390:1440,height:900},hasTouch:phone,isMobile:phone,reducedMotion:'reduce'});
        try {
          const page = await context.newPage(), errors=[];
          page.on('pageerror',error=>errors.push(error.message));
          await page.setContent(`<meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:12px}#traffic-chart{width:100%;max-width:900px}svg{display:block;width:100%;height:72px}#traffic-chart{transform:scale(.88);transform-origin:left top}.tooltip[hidden]{display:none}.chart-readout{overflow-wrap:anywhere}#traffic-legend button{min-height:40px}</style><input id="other" aria-label="Other control"><div id="traffic-chart"></div><div id="traffic-legend"></div>`);
          await page.addScriptTag({content:fs.readFileSync(path.join(root,'bobowatch/web/metrics.js'),'utf8')});
          await page.addScriptTag({content:support+'\n'+chart+'\nrender();'});
          const svg=page.locator('#traffic-chart svg'), readout=page.locator('#traffic-chart-readout');
          assert.equal(await svg.getAttribute('aria-describedby'),'traffic-chart-readout traffic-chart-instructions');
          assert.equal(await readout.getAttribute('role'),'status');
          assert.equal(await readout.getAttribute('aria-live'),'polite');
          assert.match(await readout.textContent(),/Rate: 10/);
          await svg.focus(); await page.keyboard.press('Home');
          assert.equal(await readout.getAttribute('data-at-ms'),'1000');
          assert.match(await readout.textContent(),/Rate: 0/);
          assert.doesNotMatch(await readout.textContent(),/No recorded value/);
          await page.keyboard.press('ArrowRight');
          assert.equal(await readout.getAttribute('data-at-ms'),'2000');
          assert.match(await readout.textContent(),/Rate: Unavailable \(partial telemetry\)/);
          assert.match(await readout.textContent(),/Count: 2/);
          assert.match(await readout.textContent(),/Sparse: No matching observation/);
          await page.keyboard.press('End');
          assert.equal(await readout.getAttribute('data-at-ms'),'10000');
          await page.keyboard.press('ArrowRight');
          assert.equal(await readout.getAttribute('data-at-ms'),'10000','keyboard clamps at last observation');
          await page.keyboard.press('ArrowLeft');
          assert.match(await readout.textContent(),/Rate: No recorded value/);
          await page.evaluate(()=>render());
          assert.equal(await page.evaluate(()=>document.activeElement===document.querySelector('#traffic-chart svg')),true,'focused plot remains focused after render');
          assert.equal(await readout.getAttribute('data-at-ms'),'9000','inspected timestamp survives render');
          await page.locator('#other').focus(); await page.evaluate(()=>render());
          assert.equal(await page.evaluate(()=>document.activeElement.id),'other','background render does not steal focus');
          assert.equal(await readout.getAttribute('data-at-ms'),'9000');
          // A deliberately letterboxed and transformed SVG catches DOM-rect
          // normalization errors. Screen coordinates must use its actual CTM.
          const screen = await svg.evaluate(svg=>{const p=svg.createSVGPoint(),w=svg.viewBox.baseVal.width;p.x=42+(.2*(w-12-42));p.y=80;const q=p.matrixTransform(svg.getScreenCTM());return{x:q.x,y:q.y};});
          if(phone)await page.touchscreen.tap(screen.x,screen.y);
          else await page.mouse.move(screen.x,screen.y);
          assert.equal(await readout.getAttribute('data-at-ms'),'2000',`${name} ${phone?'tap':'hover'} uses SVG coordinates`);
          await page.mouse.move(1,1);
          assert.equal(await readout.getAttribute('data-at-ms'),'2000','leaving retains accessible readout');
          await svg.focus(); await page.keyboard.press('Escape');
          assert.equal(await page.locator('#traffic-chart .tooltip').isHidden(),true);
          assert.match(await readout.textContent(),/Count: 2/,'Escape hides decoration but keeps textual values');
          await page.locator('#traffic-legend [data-series="Rate"]').click();
          assert.equal(await page.evaluate(()=>document.activeElement.dataset.series),'Rate');
          assert.equal(await page.locator('#traffic-legend [data-series="Rate"]').getAttribute('aria-pressed'),'false');
          assert.doesNotMatch(await readout.textContent(),/Rate:/);
          assert.match(await readout.textContent(),/Count: 2/);
          await page.evaluate(()=>{state.data.charts=[];render();});
          assert.match(await readout.textContent(),/No observations.*unknown/);
          assert.deepEqual(errors,[]);
          outcomes.push({browser:name,device:phone?'phone':'desktop',svg_coordinates:true,tap:phone,keyboard:true,focus_preserved:true,readout_persists:true,missing_not_zero:true,errors:0});
        } finally {await context.close();}
      }
    } finally {await browser.close();}
  }
  console.log(JSON.stringify(outcomes,null,2));
}
if (require.main === module) runIsolated().catch(error=>{console.error(error);process.exitCode=1;});


// Integration entry point for the complete ephemeral demo suite. Fixture data
// stays in page memory; restore the actual demo view even if an assertion fails.
async function runCharts({page, phone}) {
  await page.evaluate(() => {
    window.__chartQaRestore = {
      data: state.data,
      inspectedAt: document.getElementById('traffic-chart').dataset.inspectedAt,
      hostStyle: document.getElementById('traffic-chart').getAttribute('style'),
    };
    state.data = {...state.data,
      range_start_ms: 0, range_end_ms: 10000, now_ms: 10000,
      window_seconds: 10, bucket_seconds: 1, sample_seconds: 1,
      charts: [
        {at_ms:1000,rps:0,requests:0,partial:false},
        {at_ms:2000,rps:2,requests:2,partial:true},
        {at_ms:9000,rps:null,requests:3,partial:false},
        {at_ms:10000,rps:10,requests:4,partial:false},
      ]};
    window.__drawChartQa = () => lineChart('traffic-chart','Fictional chart inspection',[
      {name:'Fixture rate',color:'#123',get:p=>p.rps},
      {name:'Fixture count',color:'#456',observed:true,get:p=>p.requests},
      {name:'Fixture sparse',color:'#789',observed:true,
        points:state.data.charts.filter(p=>p.at_ms===1000||p.at_ms===10000),get:p=>p.requests},
    ],value=>String(value));
    delete document.getElementById('traffic-chart').dataset.inspectedAt;
    __drawChartQa();
  });
  try {
    const svg=page.locator('#traffic-chart svg'), readout=page.locator('#traffic-chart-readout');
    assert.equal(await svg.getAttribute('aria-describedby'),'traffic-chart-readout traffic-chart-instructions');
    assert.equal(await readout.getAttribute('role'),'status');
    assert.equal(await readout.getAttribute('aria-live'),'polite');
    await svg.focus(); await page.keyboard.press('Home');
    assert.equal(await readout.getAttribute('data-at-ms'),'1000');
    assert.match(await readout.textContent(),/Fixture rate: 0/);
    await page.keyboard.press('ArrowRight');
    assert.match(await readout.textContent(),/Fixture rate: Unavailable \(partial telemetry\)/);
    assert.match(await readout.textContent(),/Fixture count: 2/);
    assert.match(await readout.textContent(),/Fixture sparse: No matching observation/);
    await page.keyboard.press('End');
    assert.equal(await readout.getAttribute('data-at-ms'),'10000');
    await page.keyboard.press('ArrowRight');
    assert.equal(await readout.getAttribute('data-at-ms'),'10000');
    await page.keyboard.press('ArrowLeft');
    assert.match(await readout.textContent(),/Fixture rate: No recorded value/);
    await page.evaluate(()=>__drawChartQa());
    assert.equal(await page.evaluate(()=>document.activeElement===document.querySelector('#traffic-chart svg')),true);
    assert.equal(await readout.getAttribute('data-at-ms'),'9000');
    await page.locator('#project').focus(); await page.evaluate(()=>__drawChartQa());
    assert.equal(await page.evaluate(()=>document.activeElement.id),'project');
    assert.equal(await readout.getAttribute('data-at-ms'),'9000');
    // Fixed height creates letterboxing even at narrow widths. Parent transform
    // additionally catches code that incorrectly normalizes the DOM rectangle.
    await svg.evaluate(svg=>{svg.style.height='48px';svg.parentNode.style.transform='scale(.9)';svg.parentNode.style.transformOrigin='left top';});
    await svg.scrollIntoViewIfNeeded();
    const screen=await svg.evaluate(svg=>{const p=svg.createSVGPoint(),w=svg.viewBox.baseVal.width;p.x=42+.2*(w-12-42);p.y=80;const q=p.matrixTransform(svg.getScreenCTM());return{x:q.x,y:q.y};});
    if(phone)await page.touchscreen.tap(screen.x,screen.y);
    else await page.mouse.move(screen.x,screen.y);
    assert.equal(await readout.getAttribute('data-at-ms'),'2000');
    await page.mouse.move(1,1);
    assert.equal(await readout.getAttribute('data-at-ms'),'2000');
    await svg.focus();await page.keyboard.press('Escape');
    assert.equal(await page.locator('#traffic-chart .tooltip').isHidden(),true);
    assert.match(await readout.textContent(),/Fixture count: 2/);
    await page.evaluate(()=>{state.data={...state.data,charts:[]};__drawChartQa();});
    assert.match(await readout.textContent(),/No observations.*unknown/);
    return {keyboard:true,svg_coordinates:true,touch:phone,persistent_readout:true,focus_preserved:true,unknown_distinct_from_zero:true};
  } finally {
    await page.evaluate(()=>{
      const saved=window.__chartQaRestore, host=document.getElementById('traffic-chart');
      state.data=saved.data;
      if(saved.inspectedAt===undefined)delete host.dataset.inspectedAt;
      else host.dataset.inspectedAt=saved.inspectedAt;
      if(saved.hostStyle===null)host.removeAttribute('style');else host.setAttribute('style',saved.hostStyle);
      delete window.__chartQaRestore;delete window.__drawChartQa;
      render();
    });
  }
}
module.exports = {runCharts, runIsolated};
