'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {app,dialog}=require('electron');
const option=n=>process.argv.find(a=>a.startsWith('--'+n+'='))?.slice(n.length+3);
const checks=[];setTimeout(()=>app.exit(2),120000).unref();
async function run(){
 const root=path.join(option('fixtures'),'Large history'),target=path.join(option('fixtures'),'Recovered history');
 fs.mkdirSync(root,{recursive:true});fs.mkdirSync(target);fs.writeFileSync(path.join(root,'README.md'),'# Original source\nPreserve this file.');
 const seed=`import sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nfrom cc_memory_lib import knowledge_service as s\nfrom cc_memory_lib.knowledge_store import database\nr=Path(sys.argv[2])\ns.configure(r,{**s.DEFAULT,'embedding':'','automatic':False})\ns.reconcile(r)\nwith database(r) as db, db:\n source=db.execute('SELECT id FROM sources LIMIT 1').fetchone()[0]\n db.executemany('INSERT INTO revisions VALUES(?,?,?,?)',((source,format(i,'064x'),'x'*200000,'test') for i in range(360)))`;
 require('node:child_process').execFileSync(option('python'),['-I','-B','-c',seed,path.join(option('core'),'scripts'),root],{windowsHide:true,timeout:30000});
 const panel=await require(path.join(option('build'),'panel-app.cjs')).start(),web=panel.window.webContents;
 const js=s=>web.executeJavaScript(s,true);
 dialog.showOpenDialog=async()=>({canceled:false,filePaths:[root]});await js('window.panel.chooseProject()');await js("window.panel.knowledge('status')");
 const output=path.join(option('artifacts'),'large-desktop.ccmemory');dialog.showSaveDialog=async()=>({canceled:false,filePath:output});
 const start=Date.now();await js("window.panel.knowledgeArchive('backup')");assert(!panel.store.value.knowledgeError,panel.store.value.knowledgeError);
 assert(fs.statSync(output).size>64*1024*1024);checks.push('native desktop exports an archive above the old 64 MiB ceiling');
 dialog.showOpenDialog=async()=>({canceled:false,filePaths:[target]});await js('window.panel.chooseProject()');
 dialog.showOpenDialog=async()=>({canceled:false,filePaths:[output]});await js("window.panel.knowledgeArchive('restore')");assert(!panel.store.value.knowledgeError,panel.store.value.knowledgeError);
 assert(panel.store.value.knowledge.needs_reconcile&&!panel.store.value.knowledge.policy.automatic&&!panel.store.value.knowledge.policy.worker);checks.push('native restore validates before exposing dirty archive with automation disabled');
 assert(!fs.existsSync(path.join(target,'README.md')));assert.equal(fs.readFileSync(path.join(root,'README.md'),'utf8'),'# Original source\nPreserve this file.');checks.push('original documents are preserved and never fabricated in recovered project');
 fs.writeFileSync(path.join(option('artifacts'),'result.json'),JSON.stringify({passed:true,checks,archiveBytes:fs.statSync(output).size,elapsedMs:Date.now()-start},null,2));app.exit(0);
}
run().catch(error=>{fs.writeFileSync(path.join(option('artifacts'),'result.json'),JSON.stringify({passed:false,checks,error:String(error.stack)},null,2));app.exit(1);});
