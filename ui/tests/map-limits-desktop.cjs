/* Regression for bounded file maps on oversized syntax and the maintainer project. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {app}=require('electron');
const option=n=>process.argv.find(a=>a.startsWith(`--${n}=`))?.slice(n.length+3);
const checks=[],errors=[],wait=ms=>new Promise(r=>setTimeout(r,ms));
const record=(name,ok)=>{assert(ok,name);checks.push(name);};
const hash=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const timeout=setTimeout(()=>app.exit(2),90000);timeout.unref();
async function run(){
 const root=path.join(option('fixtures'),'Bounded syntax');fs.mkdirSync(root,{recursive:true});
 fs.writeFileSync(path.join(root,'large.py'),'value="PRIVATE_CANARY"\n'.repeat(6000));
 fs.writeFileSync(path.join(root,'small.py'),'def visible(): pass\n');
 const panel=await require(path.join(option('build'),'panel-app.cjs')).start(),web=panel.window.webContents,js=s=>web.executeJavaScript(s,true);
 web.on('console-message',(_e,level,message)=>{if(level>=2)errors.push(message);});
 await js('window.panel.preferences({tab:"Project Map"})');
 async function observe(project){await panel.store.select(project);await js('window.panel.mapPreview()');await js('window.panel.mapRead()');await wait(100);}
 await observe(root);
 record('isolated helper returns a map despite AST budget',panel.store.value.map?.projection.summary.units===2&&!panel.store.value.mapError);
 record('renderer announces limited detail and offending relative path',await js('document.body.innerText.includes("Limited static detail")&&document.body.innerText.includes("large.py")'));
 record('source bodies remain absent',!await js('document.body.innerText.includes("PRIVATE_CANARY")'));
 await js('document.getElementById("map-tab-code").click()');await wait(80);
 record('both files remain represented in the grid',await js('document.querySelectorAll(".code-square").length===2'));
 record('parser omission never colors files complete',await js('document.querySelectorAll(".code-square.accepted").length===0'));
 await js('document.querySelector(".code-square").focus()');await wait(50);
 record('grid explanation remains available',await js('!!document.querySelector("[role=tooltip]")'));
 const real=option('real-project');
 if(real){
  const files=['.git/HEAD','.git/index','scripts/cc.py','tests/test_cc_cli.py'];
  const before=Object.fromEntries(files.map(p=>[p,hash(path.join(real,p))]));
  const stateBefore=fs.existsSync(path.join(real,'.controlcoding'));
  await observe(real);
  const map=panel.store.value.map;
  record('actual ControlCoding root returns a current map',!!map&&!panel.store.value.mapError&&map.projection.summary.units>200);
  record('large CLI source remains in map',map.projection.bundle.sources.some(s=>s.locator.path==='scripts/cc.py'));
  record('large CLI test remains in map',map.projection.bundle.sources.some(s=>s.locator.path==='tests/test_cc_cli.py'));
  record('file overview explicitly omits symbols',map.findings.some(f=>f.code==='symbol_detail_omitted'));
  record('maintainer work is excluded',!map.projection.bundle.sources.some(s=>s.locator.path.startsWith('_work/')||s.locator.path.startsWith('devlog/')));
  await js('document.getElementById("map-tab-code").click()');await wait(100);
  record('real project grid contains every inventoried file',await js('document.querySelectorAll(".code-square").length')===map.projection.summary.units);
  record('real project completion stays unverified',await js('document.querySelectorAll(".code-square.accepted").length===0'));
  fs.writeFileSync(path.join(option('artifacts'),'controlcoding-code-grid.png'),(await web.capturePage()).toPNG());
  record('project Git/source identities preserved',files.every(p=>hash(path.join(real,p))===before[p]));
  record('observation initializes no adopter state',fs.existsSync(path.join(real,'.controlcoding'))===stateBefore);
 }
 record('renderer reports no console errors',errors.length===0);
 fs.writeFileSync(path.join(option('artifacts'),'map-limits-smoke.json'),JSON.stringify({checks,errors,versions:process.versions},null,2));
 app.exit(0);
}
run().catch(e=>{fs.writeFileSync(path.join(option('artifacts'),'failure.json'),JSON.stringify({checks,error:String(e),stack:e.stack,errors},null,2));app.exit(1);});
