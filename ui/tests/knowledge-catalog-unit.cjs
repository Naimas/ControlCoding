'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {loadCatalog}=require('../knowledge-catalog.cjs');
const page=(offset=0)=>({sources:Array.from({length:200},(_,i)=>({id:String(offset+i),title:'Document',path:`docs/${offset+i}.md`,revision:'a'.repeat(64)})),edges:[],wiki:[],conversations:[],graph:{snapshot:'a'.repeat(64),window_size:200,offset,total:5000,source_total:5000,next_offset:offset+200,external_edges:0,topics:[],query:'',topic:null,focus:null}});
test('catalog retains one bounded window and replaces it on navigation',async()=>{
 let calls=0;const call=async(a,v)=>{calls++;assert.equal(a,'graph-view');return page(v.offset);};
 const first=await loadCatalog(call,()=>true),next=await loadCatalog(call,()=>true,{offset:200,snapshot:first.graph.snapshot});
 assert.equal(first.sources.length,200);assert.equal(next.sources.length,200);assert.equal(next.sources[0].id,'200');assert.equal(calls,2);
});
test('project switch discards returned window',async()=>{
 let current=true;await assert.rejects(loadCatalog(async()=>{current=false;return page();},()=>current),/cancelled/);
});
test('changed snapshot and oversized responses fail closed',async()=>{
 await assert.rejects(loadCatalog(async()=>page(200),()=>true,{offset:200,snapshot:'b'.repeat(64)}),/snapshot_changed/);
 await assert.rejects(loadCatalog(async()=>({...page(),sources:[...page().sources,...page().sources]}),()=>true),/snapshot_changed/);
});
