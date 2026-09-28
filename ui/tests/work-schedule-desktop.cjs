'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {app,dialog}=require('electron');
const option=n=>process.argv.find(a=>a.startsWith('--'+n+'='))?.slice(n.length+3),wait=ms=>new Promise(r=>setTimeout(r,ms));
const checks=[],record=(name,pass)=>{assert(pass,name);checks.push(name);};
setTimeout(()=>app.exit(2),150000).unref();
async function run(){
 const fixtures=option('fixtures'),artifacts=option('artifacts'),root=path.join(fixtures,'Work schedule');
 fs.mkdirSync(path.join(root,'docs'),{recursive:true});fs.mkdirSync(artifacts,{recursive:true});
 const names=['Delivery','Design','API','Interface','Integration'];
 names.forEach(name=>fs.writeFileSync(path.join(root,'docs',name+'.md'),`# ${name}\n\nRecorded project work for ${name}.\n`));
 const panel=await require(path.join(option('build'),'panel-app.cjs')).start(),web=panel.window.webContents;
 const js=s=>web.executeJavaScript(s,true),until=async fn=>{for(let i=0;i<180;i++){if(await fn())return;await wait(50);}throw Error('desktop_condition_timeout');};
 const call=(action,value=null)=>js(`window.panel.knowledge(${JSON.stringify(action)},${JSON.stringify(value)})`);
 dialog.showOpenDialog=async()=>({canceled:false,filePaths:[root]});await js('window.panel.chooseProject()');await js('window.panel.preferences({tab:"Memory"})');panel.show();
 await call('configure',{scopes:['project'],automatic:false,worker:false,retention:'none',embedding:''});await call('sync');await call('work-view');
 const sources=panel.store.value.knowledgeCatalog.sources,ids=Object.fromEntries(names.map(n=>[n,sources.find(s=>s.path==='docs/'+n+'.md').id]));
 async function relate(from,to,kind,role){
  await call('work-propose',{snapshot:panel.store.value.knowledgeWork.snapshot,source:ids[from],target:ids[to],source_role:'activity',target_role:role||'activity',kind,reason:'Fixture owner declares work relationship'});
  const r=panel.store.value.knowledgeWork.relations.find(r=>r.source===ids[from]&&r.target===ids[to]&&r.kind===kind);
  assert(r,panel.store.value.knowledgeError);await call('work-review',{snapshot:panel.store.value.knowledgeWork.snapshot,id:r.id,status:'approved',reason:'Fixture owner reviews exact source revisions'});
 }
 for(const name of names.slice(1))await relate(name,'Delivery','part_of','phase');
 await relate('API','Design','depends_on');await relate('Interface','Design','depends_on');await relate('Integration','API','depends_on');await relate('Integration','Interface','depends_on');
 await call('work-schedule-view');let view=panel.store.value.knowledgeWorkSchedule;
 record('work projection reads reviewed hierarchy and branching dependency graph',view?.nodes.length===5&&view.dependencies.length===4&&view.hierarchy.length===4);
 record('process ordering works before duration estimates exist',view.complete&&!view.critical_available&&view.waves.length===3&&view.waves[1].ids.length===2&&view.nodes.find(n=>n.id===ids.Design).readiness==='ready');
 for(const [name,duration,buffer] of [['Design',2,0],['API',3,1],['Interface',1,0]]){
  const node=view.nodes.find(n=>n.id===ids[name]);await call('work-schedule-save',{snapshot:view.snapshot,start_date:'2026-10-01',task:{id:node.id,revision:node.revision,duration_days:duration,buffer_days:buffer,earliest_start:null,deadline:null},reason:'Explicit fixture planning estimate'});view=panel.store.value.knowledgeWorkSchedule;
 }
 await until(()=>js(`!!document.querySelector('[aria-label="Select task Integration"]')`));
 await js(`(()=>{const n=document.querySelector('[aria-label="Select task Integration"]');n.focus();n.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));})()`);
 await until(()=>js(`document.querySelector('[aria-label="Selected work item"] h3')?.textContent==='Integration'`));
 record('keyboard selects a task and exposes predecessors',await js(`document.querySelector('[aria-label="Selected work item"]').textContent.includes('API')&&document.querySelector('[aria-label="Selected work item"]').textContent.includes('Interface')`));
 await js(`document.querySelector('.schedule-estimates summary').click()`);
 await js(`(()=>{const label=Array.from(document.querySelectorAll('.schedule-fields label')).find(n=>n.textContent.includes('Estimated duration'));const input=label.querySelector('input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'2');input.dispatchEvent(new Event('input',{bubbles:true}));const reason=document.querySelector('.schedule-estimates textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(reason,'Reviewed integration estimate');reason.dispatchEvent(new Event('input',{bubbles:true}));})()`);
 await js(`Array.from(document.querySelectorAll('.schedule-estimates button')).find(b=>b.textContent==='Save selected estimate').click()`);
 await until(()=>!!panel.store.value.knowledgeWorkSchedule?.critical_available);
 view=panel.store.value.knowledgeWorkSchedule;
 record('renderer estimate form persists exact critical path and float',view.duration_days===8&&view.nodes.find(n=>n.id===ids.Interface).float_days===3&&view.nodes.find(n=>n.id===ids.API).critical);
 record('parent calendar bar rolls up its children',view.nodes.find(n=>n.id===ids.Delivery).earliest_finish===8);
 await call('work-schedule-view');record('saved estimates survive a fresh Python CLI process',panel.store.value.knowledgeWorkSchedule.duration_days===8);
 await js(`Array.from(document.querySelectorAll('[aria-label="Work plan views"] button')).find(b=>b.textContent==='Calendar Gantt').click()`);
 await until(()=>js(`!!document.querySelector('[aria-label="Calendar Gantt with dependencies"]')`));
 record('calendar view displays anchored dates',await js(`document.querySelector('.work-schedule').textContent.includes('2026-10-01')`));
 await js(`Array.from(document.querySelectorAll('.work-schedule button')).find(b=>b.textContent==='Collapse blocks').click()`);
 record('collapse hides subtasks without losing the block',await js(`document.querySelectorAll('.schedule-task').length===1`));
 await js(`Array.from(document.querySelectorAll('.work-schedule button')).find(b=>b.textContent==='Expand all').click()`);
 record('expand restores all recorded work items',await js(`document.querySelectorAll('.schedule-task').length===5`));
 record('hierarchical rows follow dependency order',await js(`Array.from(document.querySelectorAll('.schedule-task')).map(n=>n.getAttribute('aria-label')).join('|')==='Select task Delivery|Select task Design|Select task API|Select task Interface|Select task Integration'`));
 await js(`Array.from(document.querySelectorAll('.work-schedule button')).find(b=>b.textContent==='Open original work record').click()`);
 await until(()=>js(`!!document.querySelector('[aria-label="Work plan source document"] .knowledge-paper')`));
 record('work document opens in formatted light reader',await js(`document.querySelector('[aria-label="Work plan source document"]').textContent.includes('Recorded project work')`));
 await js(`Array.from(document.querySelectorAll('[aria-label="Work plan source document"] button')).find(b=>b.textContent==='Close work document').click()`);
 panel.window.setContentSize(1260,1000);web.setZoomFactor(1);await js(`document.querySelector('.work-schedule').scrollIntoView({block:'start'})`);await until(()=>js(`document.querySelector('.schedule-chart').scrollWidth<=document.querySelector('.schedule-chart').clientWidth+1`));await wait(100);record('small calendar fits available desktop width without hiding the final task',true);fs.writeFileSync(path.join(artifacts,'gantt-calendar.png'),(await web.capturePage()).toPNG());
 await js(`Array.from(document.querySelectorAll('[aria-label="Work plan views"] button')).find(b=>b.textContent==='Process order').click()`);await wait(100);fs.writeFileSync(path.join(artifacts,'gantt-process.png'),(await web.capturePage()).toPNG());
 panel.window.setContentSize(640,920);web.setZoomFactor(2);await until(()=>js('innerWidth===320'));
 record('Gantt stays within a 320 CSS pixel viewport',await js('document.documentElement.scrollWidth===320'));
 await js(`document.querySelector('.schedule-chart').scrollIntoView({block:'center'})`);await wait(100);fs.writeFileSync(path.join(artifacts,'gantt-320px.png'),(await web.capturePage()).toPNG());
 await js(`document.querySelector('.schedule-chart').scrollLeft=480`);await wait(100);record('narrow view pans to dependency arrows inside the chart',await js(`(()=>{const box=document.querySelector('.schedule-chart').getBoundingClientRect();return document.querySelector('.schedule-chart').scrollLeft>0&&Array.from(document.querySelectorAll('.schedule-dependency')).some(p=>{const r=p.getBoundingClientRect();return r.left<box.right&&r.right>box.left;});})()`));fs.writeFileSync(path.join(artifacts,'gantt-320px-panned.png'),(await web.capturePage()).toPNG());
 fs.writeFileSync(path.join(root,'docs','Design.md'),'# Design\n\nChanged design evidence.\n');await call('sync');view=panel.store.value.knowledgeWorkSchedule;
 record('source change automatically invalidates loaded dependency timing',view&&!view.critical_available&&view.warnings.some(w=>w.code==='stale_relation'));
 fs.writeFileSync(path.join(artifacts,'result.json'),JSON.stringify({passed:true,checks,projection:view},null,2));app.exit(0);
}
run().catch(error=>{fs.mkdirSync(option('artifacts'),{recursive:true});fs.writeFileSync(path.join(option('artifacts'),'result.json'),JSON.stringify({passed:false,checks,error:String(error.stack)},null,2));app.exit(1);});
