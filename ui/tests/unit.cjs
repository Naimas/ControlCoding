'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const { BridgeClient } = require('../bridge-client.cjs');
const { PanelState, safePreferences, visibleBounds, allowedSender } = require('../panel-state.cjs');
const path = require('node:path');

test('IPC checks exact webContents, main frame and local URL', () => {
  const frame = { url: 'cc-panel://app/index.html' }, contents = { mainFrame: frame }, window = { webContents: contents };
  assert(allowedSender({ sender: contents, senderFrame: frame }, window, frame.url));
  assert(!allowedSender({ sender: {}, senderFrame: frame }, window, frame.url));
  assert(!allowedSender({ sender: contents, senderFrame: {url:frame.url} }, window, frame.url));
  assert(!allowedSender({ sender: contents, senderFrame: frame }, window, 'https://foreign.invalid'));
});
test('window preferences clamp geometry and restore off-screen bounds', () => {
  const prefs = safePreferences({ pinned: 'true', bounds: {x:99999,y:-8888,width:99999,height:2}, tab:'CANARY', shortcut:'launch.exe' });
  assert.equal(prefs.pinned,false); assert.equal(prefs.tab,'Status');
  const bounds = visibleBounds(prefs.bounds,[{workArea:{x:0,y:0,width:1200,height:800}}],false);
  assert(bounds.x>=0 && bounds.y>=0 && bounds.x+bounds.width<=1200 && bounds.y+bounds.height<=800);
});
test('late old-project responses cannot overwrite the current project', async () => {
  const responses=[]; const client={cancel(){},run(){return new Promise(resolve=>responses.push(resolve));}};
  const model=new PanelState(client);const old=model.select('old');const current=model.select('new');
  responses[1]({status:'ok',result:{marker:'new'},observed_at:'now'});await current;
  responses[0]({status:'ok',result:{marker:'old'},observed_at:'earlier'});await old;
  assert.equal(model.value.project,'new');assert.equal(model.value.state.marker,'new');assert.equal(model.value.activity.length,1);
});
test('failed refresh removes old observations and preview; activity is bounded',async()=>{
  let good=true;const model=new PanelState({cancel(){},async run(){return good?{status:'ok',result:{},observed_at:'now'}:{status:'error',error:{code:'inaccessible'}};}});
  await model.select('project');await model.observe('preview');good=false;await model.observe('read');
  assert.equal(model.value.state,null);assert.equal(model.value.preview,null);assert.equal(model.value.observedAt,null);
  for(let n=0;n<110;n++)await model.observe('read');assert.equal(model.value.activity.length,100);
});
function fakeProcess() {
  const p=new EventEmitter();p.stdin=new PassThrough();p.stdout=new PassThrough();p.stderr=new PassThrough();p.exitCode=null;
  p.kill=()=>{p.killed=true;p.exitCode=-1;queueMicrotask(()=>p.emit('close',-1));}; return p;
}
const runtime=path.resolve('python.exe'),script=path.resolve('cc_panel_bridge.py'),root=path.resolve('fixture');
test('spawn uses isolated Python, array args, no shell and no credentials in environment',async()=>{
  let options;const p=fakeProcess();const client=new BridgeClient(runtime,script,{spawn(exe,args,opts){assert.equal(exe,runtime);assert.deepEqual(args,['-I','-B',script]);options=opts;return p;}});
  const answer=client.run('read',root);let request='';p.stdin.on('data',x=>request+=x);
  await new Promise(r=>setImmediate(r));const parsed=JSON.parse(request);
  p.stdout.write(JSON.stringify({version:1,id:parsed.id,operation:'read',status:'ok',result:{project_root:root}}));p.exitCode=0;p.emit('close',0);
  assert.equal((await answer).status,'ok');assert.equal(options.shell,false);assert.equal(options.windowsHide,true);
  assert(!('PATH' in options.env));assert(!('OPENAI_API_KEY' in options.env));assert(!('PYTHONPATH' in options.env));
});
test('timeout and oversized output kill only the owned helper',async()=>{
  const p=fakeProcess();const c=new BridgeClient(runtime,script,{spawn:()=>p,timeout:10});
  assert.equal((await c.run('read',root)).error.code,'helper_timeout');assert(p.killed);
  const other=fakeProcess();c.spawn=()=>other;const answer=c.run('preview',root);await new Promise(r=>setImmediate(r));other.stdout.write(Buffer.alloc(1024*1024+2));
  assert.equal((await answer).error.code,'helper_output_limit');assert(other.killed);
});
test('bad helper protocol and stderr are never echoed',async()=>{
  const p=fakeProcess();const c=new BridgeClient(runtime,script,{spawn:()=>p});const answer=c.run('read',root);
  await new Promise(r=>setImmediate(r));p.stderr.write('CANARY');p.exitCode=1;p.emit('close',1);const response=await answer;
  assert.equal(response.error.code,'helper_failure');assert(!JSON.stringify(response).includes('CANARY'));
});
test('replacement helper waits for owned predecessor to exit',async()=>{
  const children=[];const c=new BridgeClient(runtime,script,{spawn(){const p=fakeProcess();p.kill=()=>{p.killed=true;};children.push(p);return p;}});
  const first=c.run('read',root);await new Promise(r=>setImmediate(r));
  const second=c.run('read',root);await new Promise(r=>setImmediate(r));
  assert.equal(children.length,1);assert(children[0].killed);assert.equal((await first).error.code,'cancelled');
  children[0].exitCode=-1;children[0].emit('close',-1);await new Promise(r=>setImmediate(r));
  assert.equal(children.length,2);c.cancel();children[1].exitCode=-1;children[1].emit('close',-1);assert.equal((await second).error.code,'cancelled');
});
test('invalid helper identity cannot become project state',async()=>{
  const p=fakeProcess();const c=new BridgeClient(runtime,script,{spawn:()=>p});const answer=c.run('read',root);
  await new Promise(r=>setImmediate(r));p.stdout.write(JSON.stringify({version:1,id:'wrong',operation:'read',status:'ok',result:{project_root:root}}));
  p.exitCode=0;p.emit('close',0);assert.equal((await answer).error.code,'invalid_helper_response');
});
