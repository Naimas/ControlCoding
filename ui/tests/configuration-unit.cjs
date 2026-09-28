'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {PanelState}=require('../panel-state.cjs');
const {BridgeClient}=require('../bridge-client.cjs');
const {fields,validDraft,validRequest,validResult}=require('../config-contract.cjs');
const hash='a'.repeat(64);
const draft=()=>({schema_version:1,name:'Example',kind:'existing',goal:'',user_host:'other',documentation_mode:'managed',memory_policy:'deferred',design_paths:[],decisions:Object.fromEntries(fields.map(f=>[f,{mode:'defer',status:'unset',value:'',rationale:'',evidence:[],analysis_id:''}]))});
const current=()=>({draft:draft(),saved:false,revision:'absent',path:'.controlcoding/panel-setup-draft.json'});
const plan=()=>({schema_version:1,root:'/one',operation:'save',draft:draft(),revision:'absent',approval_id:hash,write_supported:true,files:[],blockers:[],notices:[],configured_not_verified:true});
test('draft and transport reject extra fields, oversized payloads and supplied paths',async()=>{
 assert(validDraft(draft()));
 for(const patch of [{path:'elsewhere'},{goal:1},{name:''},{schema_version:true},{decisions:{}},{design_paths:new Array(17).fill('x')}])assert(!validDraft({...draft(),...patch}));
 assert(validRequest('config_preview_v1',{draft:draft(),intent:'save',revision:'absent'}));
 assert(!validRequest('config_commit_v1',{draft:draft(),intent:'save',revision:'absent'}));
 let calls=0;const client=new BridgeClient(process.execPath,__filename,{spawn:()=>calls++});
 assert.equal((await client.run('config_commit_v1',process.cwd(),{project_root:'/other'})).error.code,'invalid_request');assert.equal(calls,0);
 assert(validResult('config_preview_v1',{project_root:'/one',config_preview:plan()},{intent:'save',revision:'absent'}));
 assert(!validResult('config_preview_v1',{project_root:'/one',config_preview:{...plan(),operation:'apply'}},{intent:'save',revision:'absent'}));
});
test('commit needs a current main-owned preview; edits discard it',async()=>{
 const calls=[];const client={cancel(){},async run(op,root,o){calls.push({op,root,o});return {status:'ok',result:op==='config_read_v1'?{configuration:current()}:op==='config_preview_v1'?{config_preview:plan()}:{} };}};
 const s=new PanelState(client);await s.select('/one');await s.configuration.run('read');
 await s.configuration.run('commit');assert.equal(calls.length,2);
 await s.configuration.run('preview',draft(),'save');assert(s.configuration.pending);
 s.configuration.discard();await s.configuration.run('commit');assert.equal(calls.length,3);assert.equal(s.value.configPreview,null);s.refresh.reset();
});
test('project changes reject late configuration results',async()=>{
 let release;const client={cancel(){},run(op){return op==='config_read_v1'?new Promise(r=>release=r):Promise.resolve({status:'ok',result:{}});}};
 const s=new PanelState(client);await s.select('/one');const pending=s.configuration.run('read');await s.select('/two');
 release({status:'ok',result:{configuration:current()}});await pending;assert.equal(s.value.configuration,null);assert.equal(s.value.project,'/two');s.refresh.reset();
});
test('commit binds project and blocks switching while helper writes',async()=>{
 let release,request;const client={cancel(){},run(op,root,o){if(op==='config_commit_v1'){request={root,o};return new Promise(r=>release=r);}return Promise.resolve({status:'ok',result:op==='config_read_v1'?{configuration:current()}:op==='config_preview_v1'?{config_preview:plan()}:{} });}};
 const s=new PanelState(client);await s.select('/one');await s.configuration.run('read');await s.configuration.run('preview',draft(),'save');
 const saving=s.configuration.run('commit');await s.select('/two');assert.equal(s.value.project,'/one');assert(s.value.committing);assert.equal(request.o.approval_id,hash);
 release({status:'ok',result:{config_saved:{saved:true,revision:hash,operation:'save',files:['draft']}}});await saving;
 assert(!s.value.committing);assert.equal(s.value.configuration.revision,hash);assert.equal(s.configuration.pending,null);assert(!JSON.stringify(s.value.activity).includes('Example'));s.refresh.reset();
});
test('failure clears preview, approval and prior success',async()=>{
 let fail=false;const client={cancel(){},async run(op){return fail?{status:'error',error:{code:'preview_mismatch'}}:{status:'ok',result:op==='config_read_v1'?{configuration:current()}:op==='config_preview_v1'?{config_preview:plan()}:{} };}};
 const s=new PanelState(client);await s.select('/one');await s.configuration.run('read');await s.configuration.run('preview',draft(),'save');fail=true;
 await s.configuration.run('commit');assert.equal(s.value.configError.code,'preview_mismatch');assert.equal(s.value.configPreview,null);assert.equal(s.value.configSaved,null);assert.equal(s.configuration.pending,null);s.refresh.reset();
});
