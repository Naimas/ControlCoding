'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {layout,zoomAt,fitRect,track}=require('../knowledge-layout.cjs');
const nodes=Array.from({length:200},(_,i)=>({id:'n'+i,path:'docs/'+i+'.md',title:'Document '+i,area:'documentation'}));
const edges=nodes.slice(1).map((n,i)=>({source:nodes[i].id,target:n.id}));

test('large circuit reveals bounded detail without moving or deleting model records',()=>{
 const {visibleRecords}=require('../knowledge-layout.cjs');
 const large=layout(Array.from({length:5000},(_,i)=>({id:'n'+i,path:'docs/'+i+'.md',title:'Plan '+i,area:'plans'})),[]);
 const overview=visibleRecords(large.records,{x:0,y:0,scale:.1},{w:1000,h:620});
 assert(overview.length<=256);assert.equal(large.records.length,5000);
 const target=large.records[4500],view=fitRect(target,1000,620),focused=visibleRecords(large.records,view,{w:1000,h:620},target.id);
 assert(focused.some(n=>n.id===target.id));assert(focused.length<=256);assert.deepEqual(focused.find(n=>n.id===target.id),target);
});
test('all records coexist around named topic hubs without paging or duplicate positions',()=>{const m=layout(nodes,edges);assert.equal(m.records.length,200);assert.equal(new Set(m.records.map(n=>n.id)).size,200);assert(m.clusters.every(c=>c.hub&&c.title!=='Link neighborhood'));for(const n of m.records){const c=m.clusters.find(c=>c.id===n.cluster);assert(n.w>0&&n.h>0);assert(n.x>=c.x&&n.x+n.w<=c.x+c.w);assert(n.y>=c.y&&n.y+n.h<=c.y+c.h);}assert.equal(m.tiles.length,6);});
test('layout is deterministic regardless of input order and aggregate links only represent real edges',()=>{assert.deepEqual(layout(nodes,edges),layout([...nodes].reverse(),[...edges].reverse()));const m=layout(nodes,edges),membership=new Map(m.records.map(n=>[n.id,n.cluster]));const crossing=edges.filter(e=>membership.get(e.source)!==membership.get(e.target)).length;assert.equal(m.edges.reduce((sum,e)=>sum+e.count,0),crossing);assert.equal(layout(nodes,[]).edges.length,0);});
test('pointer anchored zoom preserves the world coordinate under the pointer and clamps scale',()=>{const v={x:40,y:-120,scale:.6},p={x:250,y:300},n=zoomAt(v,p,2);assert.equal((p.x-v.x)/v.scale,(p.x-n.x)/n.scale);assert.equal((p.y-v.y)/v.scale,(p.y-n.y)/n.scale);assert.equal(zoomAt(v,p,1e6).scale,128);assert.equal(zoomAt(v,p,.001).scale,.06);});
test('fit camera centers the chosen region without changing layout; tracks are rounded orthogonal',()=>{const r={x:100,y:200,w:800,h:600},c=fitRect(r,1000,700);assert.equal(c.x+(r.x+r.w/2)*c.scale,500);assert.equal(c.y+(r.y+r.h/2)*c.scale,350);const d=track({x:0,y:0,w:100,h:50},{x:400,y:200,w:80,h:50});assert(d.includes('Q'));assert(!/NaN|Infinity/.test(d));});
