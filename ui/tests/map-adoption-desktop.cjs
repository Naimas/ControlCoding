/* Maintainer-run adoption/reopen of an extracted portable artifact. External fixtures only. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {app,dialog}=require('electron');
const option=n=>process.argv.find(a=>a.startsWith(`--${n}=`))?.slice(n.length+3);
const build=option('build'),artifacts=option('artifacts'),fixtures=option('fixtures'),reopen=option('phase')==='reopen';
const root=path.join(fixtures,'Existing project café'),definition=path.join(root,'.controlcoding/project-map/definition.json');
const checks=[],errors=[],wait=ms=>new Promise(r=>setTimeout(r,ms));
const record=(name,value)=>{assert(value,name);checks.push(name);};
const write=(name,value)=>{const p=path.join(root,name);fs.mkdirSync(path.dirname(p),{recursive:true});fs.writeFileSync(p,typeof value==='string'?value:JSON.stringify(value));};
function inventory(){const result={};for(const entry of fs.readdirSync(root,{recursive:true,withFileTypes:true})){if(entry.isFile()){const p=path.join(entry.parentPath,entry.name);result[path.relative(root,p)]=crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');}}return result;}
const deadline=setTimeout(()=>{fs.writeFileSync(path.join(artifacts,'adoption-timeout.json'),JSON.stringify({checks}));app.exit(1);},90000);deadline.unref();
async function run(){
 if(!reopen){
  fs.mkdirSync(root,{recursive:true});
  write('api/service.py','def serve(): return 1\n');
  write('web/view.ts','import { missing } from "./missing";\nexport function render(){return missing;}\n');
  write('docs/design.md','# Service\n## Client\nPrivate body CANARY_ADOPTION\n');
  write('.env','CANARY_PRIVATE_TOKEN');
  write('.controlcoding/features/features.json',{schemaVersion:'cc-feature-state/v1',wipLimit:3,features:[{id:'api',title:'API feature',state:'blocked',acceptanceCriteria:['CANARY_CRITERION']}]});
  write('.controlcoding/cc_config.json',{protected_zones:{deny:['api/service.py']}});
 }
 const before=inventory(),saved=reopen?fs.readFileSync(definition,'utf8'):null;
 const panel=await require(path.join(build,'panel-app.cjs')).start(),web=panel.window.webContents;
 const js=code=>web.executeJavaScript(code,true),map=()=>panel.store.value.map;
 web.on('console-message',(_e,level,message)=>{if(level>=2)errors.push(message);});
 record('new process has no automatic consent or history',!panel.store.value.mapRefresh.enabled&&panel.store.value.mapHistory.length===0);
 if(reopen)record('external profile retains chosen theme',panel.snapshot().preferences.theme==='light');
 dialog.showOpenDialog=async()=>({canceled:false,filePaths:[root]});await js('window.panel.chooseProject()');
 await js('window.panel.preferences({tab:"Project Map"})');
 dialog.showOpenDialog=async()=>({canceled:false,filePaths:[path.join(root,'docs/design.md')]});await js('window.panel.chooseDesign()');
 await js('window.panel.mapPreview()');record('preview does not read source bodies into a map',!map()&&!!panel.store.value.mapScope);
 await js('window.panel.mapRead()');record('extracted Core observes existing code and explicit design',!!map()?.review&&panel.store.value.designPaths.length===1&&map().projection.bundle.nodes.some(n=>n.title==='service.py'));
 if(!reopen){
  const target=map().projection.bundle.nodes.find(n=>n.title==='service.py').id;
  await panel.store.review({operation:'accept',target});await panel.store.saveReview();
 } else record('reviewed identity survives process restart and package replacement',map().projection.bundle.nodes.some(n=>n.title==='service.py'&&n.mapping==='confirmed')&&fs.readFileSync(definition,'utf8')===saved);
 record('mapping transaction leaves only the reviewed definition',fs.readdirSync(path.dirname(definition)).join()==='definition.json');
 await js('window.panel.controlsPreview()');await js('window.panel.controlsRead()');
 await js('window.panel.analysisPreview()');await js('window.panel.analysisRead()');
 record('packaged Python and Babel return both overlays',!!map()?.controls&&!!map()?.analysis);
 record('feature blocker remains distinct from code completion',map().projection.statuses.some(s=>s.impediments.includes('feature_reported_blocked'))&&map().projection.summary.verified_units===0);
 await js('document.getElementById("map-tab-code").click()');await wait(100);
 await js('document.querySelector(".code-square").focus()');await wait(100);
 record('grid explains status through keyboard popup',await js('!!document.querySelector("[role=tooltip]")'));
 await js('document.querySelector(".code-square").click()');await wait(100);
 record('square selection pins the status explanation',await js('document.querySelector(".map-inspector").innerText.includes("Why this status")'));
 await js('window.panel.mapAuto()');record('automatic observation requires fresh explicit consent',panel.store.value.mapRefresh.enabled);
 await js('window.panel.mapAuto()');record('pause disposes owned watchers',!panel.store.refresh.watcher);
 await js('window.panel.preferences({theme:"light"})');
 fs.writeFileSync(path.join(artifacts,'adoption-grid.png'),(await web.capturePage()).toPNG());
 record('no private source bodies or criteria in renderer',!await js('document.body.innerText.includes("CANARY_")'));
 const after=inventory();
 record('adoption preserves every existing source and private file',Object.entries(before).every(([p,h])=>after[p]===h));
 record('only an explicitly reviewed definition may be added',Object.keys(after).filter(p=>!Object.hasOwn(before,p)).every(p=>p.replaceAll('\\','/')==='.controlcoding/project-map/definition.json'));
 record('renderer remains sandboxed without arbitrary commands',await js('typeof require==="undefined"&&typeof window.panel.apply==="undefined"'));
 record('no renderer errors',errors.length===0);
 fs.writeFileSync(path.join(artifacts,'adoption-smoke.json'),JSON.stringify({phase:reopen?'reopen':'adopt',versions:process.versions,checks,errors,sourceHashes:after},null,2));app.quit();
}
run().catch(error=>{fs.writeFileSync(path.join(artifacts,'adoption-failure.json'),JSON.stringify({checks,errors,error:String(error),stack:error.stack},null,2));app.exit(1);});
