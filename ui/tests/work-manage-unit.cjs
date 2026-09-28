'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {PanelState}=require('../panel-state.cjs');
const {validValue,validRequest,validResult}=require('../work-manage.cjs');
const value={action:'init',name:'Example',purpose:'Project knowledge'},hash='a'.repeat(64);
const preview={schema_version:1,action:'init',root:'/one',approval_id:hash,files:[],sources:[],blockers:[],write_supported:true,notice:'Notice'};
test('memory management narrows payloads and result schemas',()=>{
 assert(validValue(value));assert(!validValue({...value,path:'escape'}));assert(!validValue({action:'capture',title:'x',body:'y',area:'../escape'}));
 const o={value,source_paths:[],request_id:'a'.repeat(32),timestamp:'2026-09-22T12:00:00Z'};
 assert(validRequest('work_manage_preview_v1',o));assert(!validRequest('work_manage_commit_v1',o));
 assert(validResult('work_manage_preview_v1',{project_root:'/one',work_manage_preview:preview},o));
 assert(!validResult('work_manage_preview_v1',{project_root:'/two',work_manage_preview:preview},o));
 assert(!validResult('work_manage_commit_v1',{work_manage_saved:{saved:true,verified:true,action:'init',files:[],directories_created:[]}},o));
});
test('pending memory approval is discarded by edits and project switching',async()=>{
 const client={cancel(){},async run(op){return {status:'ok',result:op==='work_manage_preview_v1'?{work_manage_preview:preview}:{}};}};
 const s=new PanelState(client);await s.select('/one');await s.workManagement.run('preview',value);assert(s.workManagement.pending);
 s.workManagement.discard();assert.equal(s.workManagement.pending,null);await s.workManagement.run('commit');assert.equal(s.value.workManageSaved,null);
 s.workManagement.selectSources(['brief.md']);await s.select('/two');assert.deepEqual(s.value.workImportPaths,[]);assert.equal(s.value.workManagePreview,null);s.refresh.reset();
});
test('commit owns the project and refreshes memory after saving',async()=>{
 let release;const calls=[];
 const client={cancel(){},run(op,root,o){calls.push({op,root,o});if(op==='work_manage_commit_v1')return new Promise(r=>release=r);return Promise.resolve({status:'ok',result:op==='work_manage_preview_v1'?{work_manage_preview:preview}:op==='work_preview_v1'?{work_scope:{scope_id:hash}}:op==='work_read_v1'?{work:{state:'present'}}:{}});}};
 const s=new PanelState(client);await s.select('/one');await s.workManagement.run('preview',value);const pending=s.workManagement.run('commit');
 assert(s.value.committing);await s.select('/two');assert.equal(s.value.project,'/one');
 release({status:'ok',result:{work_manage_saved:{saved:true,verified:false,files:['CONTROLWORK.md']}}});await pending;
 assert(!s.value.committing);assert.equal(s.value.work.state,'present');assert.deepEqual(calls.slice(-2).map(c=>c.op),['work_preview_v1','work_read_v1']);
 assert(!JSON.stringify(s.value.activity).includes('Project knowledge'));s.refresh.reset();
});
test('late memory previews cannot cross projects',async()=>{
 let release;const client={cancel(){},run(op){return op==='work_manage_preview_v1'?new Promise(r=>release=r):Promise.resolve({status:'ok',result:{}});}};
 const s=new PanelState(client);await s.select('/one');const pending=s.workManagement.run('preview',value);await s.select('/two');
 release({status:'ok',result:{work_manage_preview:preview}});await pending;assert.equal(s.value.workManagePreview,null);assert.equal(s.workManagement.pending,null);s.refresh.reset();
});
