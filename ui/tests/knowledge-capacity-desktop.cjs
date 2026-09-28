'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {app}=require('electron');
const option=n=>process.argv.find(a=>a.startsWith('--'+n+'='))?.slice(n.length+3),wait=ms=>new Promise(r=>setTimeout(r,ms));
setTimeout(()=>app.exit(2),90000).unref();
async function run(){
 const panel=await require(path.join(option('build'),'panel-app.cjs')).start();
 const web=panel.window.webContents,js=s=>web.executeJavaScript(s,true);
 await panel.store.knowledge.run('status');await panel.store.knowledge.run('catalog');
 await panel.store.knowledge.run('library',{kind:'sources',query:'',offset:0,snapshot:null});
 await js('window.panel.preferences({tab:"Memory"})');panel.show();await wait(200);
 assert.equal(panel.store.value.knowledge.counts.chunks,50000);
 assert.equal(panel.store.value.knowledge.counts.conversations,1000);
 assert.equal(panel.store.value.knowledgeCatalog.sources.length,200);
 const feedbackMs=await js(`new Promise((resolve,reject)=>{
  const start=performance.now();let timer;
  const observer=new MutationObserver(()=>{if([...document.querySelectorAll('button')].some(b=>b.textContent==='Stop operation')){observer.disconnect();clearTimeout(timer);resolve(performance.now()-start);}});
  observer.observe(document.body,{childList:true,subtree:true});
  timer=setTimeout(()=>{observer.disconnect();reject(Error('feedback deadline'));},2000);
  window.panel.knowledge('sync').catch(reject);
 })`);
 assert(feedbackMs<=250,'UI feedback exceeds 250ms');
 while(panel.store.value.knowledgeBusy)await wait(50);
 assert(!panel.store.value.knowledgeError);
 const catalog=panel.store.value.knowledgeCatalog;
 await panel.store.knowledge.run('graph-view',{topic:'reference',query:'Source 4999',offset:0,snapshot:null,focus:null});
 assert.equal(panel.store.value.knowledgeCatalog.sources.length,1);
 assert(panel.store.value.knowledgeCatalog.sources[0].path.endsWith('item-4999.md'));
 const report={passed:true,feedback_ms:feedbackMs,counts:panel.store.value.knowledge.counts,
  retained_source_nodes:catalog.sources.length,process_metrics:app.getAppMetrics()};
 fs.writeFileSync(path.join(option('artifacts'),'capacity-desktop.json'),JSON.stringify(report,null,2));
 fs.writeFileSync(path.join(option('artifacts'),'capacity-desktop.png'),(await web.capturePage()).toPNG());
 panel.store.knowledge.dispose();panel.store.refresh.dispose();app.exit(0);
}
run().catch(error=>{fs.writeFileSync(path.join(option('artifacts'),'capacity-desktop.json'),JSON.stringify({passed:false,error:String(error.stack)},null,2));app.exit(1);});
