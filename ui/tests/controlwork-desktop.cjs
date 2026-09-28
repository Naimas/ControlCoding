/* Real Electron, isolated Core reads, canonical external fixture records. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {app,dialog}=require('electron');
const option=n=>process.argv.find(a=>a.startsWith('--'+n+'='))?.slice(n.length+3);
const checks=[],errors=[],wait=ms=>new Promise(r=>setTimeout(r,ms));
const record=(name,value)=>{assert(value,name);checks.push(name);};
function inventory(root){return Object.fromEntries(fs.readdirSync(root).sort().map(name=>{const p=path.join(root,name);return [name,fs.statSync(p).isDirectory()?inventory(p):crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex')];}));}
function write(root,name,value){const p=path.join(root,name);fs.mkdirSync(path.dirname(p),{recursive:true});fs.writeFileSync(p,typeof value==='string'?value:JSON.stringify(value));}
const timeout=setTimeout(()=>app.exit(2),90000);timeout.unref();
async function run(){
 const root=path.join(option('fixtures'),'ControlWork example project'),empty=path.join(option('fixtures'),'Empty project');
 fs.mkdirSync(root,{recursive:true});fs.mkdirSync(empty,{recursive:true});
 write(root,'CONTROLWORK.md','# Example knowledge project\nLocal demonstration fixture, not production project memory.');
 for(const [area,title,state,body] of [
  ['sources','Product brief','active','A desktop tool for inspecting project knowledge.'],
  ['decisions','Architecture decision','active','Keep project truth separate from presentation.'],
  ['plans','Architecture next steps','needs_review','Verify source ownership and review relationships.'],
  ['notes','Interface notes','captured','A shared interface links the observer and the Core.'],
  ['legacy','Architecture previous design','legacy','Superseded approach retained for history.']])
  write(root,`.controlwork/memory/${area}/${area}.md`,`# ${title}\n\n- **Area**: ${area}\n- **Lifecycle**: ${state}\n- **Category**: architecture\n- **Source**: project-design.md\n\n## Body\n${body}\n`);
 write(root,'.controlwork/memory/notes/literal.md','# <script>window.PWNED=true</script>\n## Body\n<img src=x onerror="window.PWNED=true">');
 write(root,'.controlwork/sessions/session-one.json',{schemaVersion:1,id:'session-one',topic:'Architecture review',status:'completed',updatedAt:'2026-09-22T10:00:00Z',summary:'Reviewed ownership and agreed the next verification step.',decisions:['Use the Core as the source of truth'],followups:['Verify the interface'],links:[{type:'references',target:'.controlwork/memory/decisions/decisions.md',targetType:'decision'}]});
 write(root,'.controlwork/checkpoints/checkpoint.md','# Review checkpoint\nSource ownership review recorded.');
 write(root,'.controlwork/context-packets/previous.md','# Architecture context packet\nSaved context with source references.');
 write(root,'.env','PRIVATE_CANARY');
 const before=inventory(root),panel=await require(path.join(option('build'),'panel-app.cjs')).start(),web=panel.window.webContents;
 const js=code=>web.executeJavaScript(code,true),shot=async name=>{await wait(100);fs.writeFileSync(path.join(option('artifacts'),name+'.png'),(await web.capturePage()).toPNG());};
 web.on('console-message',(_e,level,message)=>{if(level>=2)errors.push(message);});
 dialog.showOpenDialog=async()=>({canceled:false,filePaths:[root]});
 await js('window.panel.chooseProject()');await js('window.panel.preferences({tab:"Memory"})');await wait(100);
 record('ControlWork replaces the Memory placeholder',await js('document.querySelector("h1").textContent==="ControlWork"'));
 record('navigation does not read project knowledge',panel.store.value.work===null&&panel.store.value.workScope===null);
 await js('window.panel.workPreview()');await wait(60);
 record('explicit read scope precedes text access',panel.store.value.work===null&&await js('document.body.innerText.includes("CONTROLWORK.md")'));
 await js('window.panel.workRead("")');await wait(100);
 record('real Core memory and sessions arrive through IPC',panel.store.value.work?.documents.length===9&&panel.store.value.work?.sessions.length===1);
 record('recorded relationship is preserved',panel.store.value.work.graph.edges.some(e=>e.confidence==='explicit_link'));
 record('unselected private contents stay out of renderer',!await js('document.body.innerText.includes("PRIVATE_CANARY")'));
 await js('document.getElementById("cw-tab-1").click()');await wait(60);
 await js('document.querySelector(".cw-record").click()');await wait(60);
 record('documents have source text inspection',await js('!!document.querySelector(".cw-detail .cw-text")'));
 await js('document.querySelector(".cw-tabs").scrollIntoView({block:"start"})');await shot('documents-dark');
 await js('Array.from(document.querySelectorAll(".cw-record")).find(b=>b.textContent.includes("<script>")).click()');await wait(60);
 record('HTML-like memory is inert literal text',await js('window.PWNED===undefined&&document.querySelector(".cw-detail").innerText.includes("<img")&&!document.querySelector(".cw-detail img")'));
 await js('document.getElementById("cw-tab-3").click()');await wait(60);await js('document.querySelector(".cw-record").click()');await wait(60);
 record('sessions show decisions and followups',await js('document.querySelector(".cw-detail").innerText.includes("Verify the interface")'));
 record('session summaries never claim full chat capture',await js('document.querySelector(".cw-detail").innerText.includes("full conversation transcript is not available")'));
 await shot('session');
 await js('document.getElementById("cw-tab-2").click()');await wait(60);
 record('graph exposes keyboard-operable records',await js('document.querySelectorAll(".cw-graph g[role=button][tabindex]").length===10'));
 await js('document.querySelector(".cw-graph g").dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true}))');await wait(60);
 record('keyboard graph selection opens source detail',await js('!!document.querySelector(".cw-detail h3")'));
 await shot('graph-dark');
 await js('document.getElementById("cw-tab-5").click()');await js('window.panel.workRead("Architecture")');await wait(80);
 record('Core retrieval generates cited packet',panel.store.value.work.packet.citations.length>0&&await js('document.body.innerText.includes("RAG context packet")'));
 record('legacy not credited as current retrieval',panel.store.value.work.packet.citations.every(c=>c.lifecycle!=='legacy'));
 record('query not persisted in panel activity',!JSON.stringify(panel.store.value.activity).includes('Architecture'));
 await shot('retrieval');
 record('IPC rejects unsolicited memory scope paths',await js('window.panel.workRead({query:"x",project_root:"elsewhere"}).then(()=>false,()=>true)'));
 record('preload rejects preview arguments',await js('window.panel.workPreview("elsewhere").then(()=>false,()=>true)'));
 record('renderer has no generic shell or unreviewed initialization API',await js('typeof require==="undefined"&&typeof window.panel.workInit==="undefined"'));
 panel.window.setContentSize(400,620);web.setZoomFactor(1.25);
 const deadline=Date.now()+3000;while(await js('innerWidth')!==320&&Date.now()<deadline)await wait(30);
 for(let index=0;index<6;index++){
  await js(`document.getElementById('cw-tab-${index}').click()`);await wait(50);
  record('view '+index+' fits 320 CSS pixels',await js('innerWidth===320&&document.documentElement.scrollWidth===320'));
 }
 await js('document.querySelector(".cw-tabs").scrollIntoView({block:"start"})');await shot('controlwork-320');
 web.setZoomFactor(1);panel.window.setContentSize(1150,820);await js('window.panel.preferences({theme:"light"})');await wait(100);
 await js('document.getElementById("cw-tab-1").click()');await shot('documents-light');
 record('every adopter byte remains unchanged',JSON.stringify(inventory(root))===JSON.stringify(before));
 dialog.showOpenDialog=async()=>({canceled:false,filePaths:[empty]});await js('window.panel.chooseProject()');await wait(60);
 record('project switch clears previous memory and scope',panel.store.value.work===null&&panel.store.value.workScope===null);
 await js('window.panel.workPreview()');await js('window.panel.workRead("")');await wait(60);
 record('empty project explains absent archive',await js('document.body.innerText.includes("No ControlWork archive found")'));
 record('empty project is not initialized',fs.readdirSync(empty).length===0);
 write(empty,'.controlwork/sessions/bad.json','{private malformed body');await js('window.panel.workRead("")');await wait(60);
 record('corrupt refresh clears current results',panel.store.value.work===null&&panel.store.value.workError?.code==='invalid_memory');
 record('corrupt source contents are not error text',!await js('document.body.innerText.includes("private malformed body")'));
 record('no renderer errors',errors.length===0);
 fs.writeFileSync(path.join(option('artifacts'),'controlwork-smoke.json'),JSON.stringify({checks,errors,versions:process.versions},null,2));app.exit(0);
}
run().catch(e=>{fs.writeFileSync(path.join(option('artifacts'),'failure.json'),JSON.stringify({checks,error:String(e),stack:e.stack,errors},null,2));app.exit(1);});
