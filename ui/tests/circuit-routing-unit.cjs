'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {selectionScope,routeConnections,blocked,sharedLength}=require('../circuit-routing.cjs');
const rect=(id,x,y,w=100,h=60)=>({id,x,y,w,h});
const connection=(a,b,key)=>({a,b,key,kind:'reference',count:1,area:'documentation',highlight:false});
test('routes detour around an intervening chip instead of crossing its label',()=>{
 const a=rect('a',0,100),b=rect('b',400,100),wall=rect('wall',180,60,120,160),r=routeConnections([connection(a,b,'ab')],[a,b,wall]);
 assert.equal(r.omitted.length,0);const points=r.routes[0].points;assert(!blocked(points,[wall]));assert(r.routes[0].d.includes('Q'));assert(points.every((p,i)=>!i||p[0]===points[i-1][0]||p[1]===points[i-1][1]));
});
test('fan-out uses distinct endpoint ports and avoids coincident trunks',()=>{
 const a=rect('a',0,100,100,180),targets=[rect('b',420,0),rect('c',420,150),rect('d',420,300)],r=routeConnections(targets.map(b=>connection(a,b,a.id+b.id)),[a,...targets]);
 assert.equal(r.routes.length,3);assert.equal(new Set(r.routes.map(e=>JSON.stringify(e.points[0]))).size,3);let shared=0;for(let i=0;i<r.routes.length;i++)for(let j=i+1;j<r.routes.length;j++)for(const a of r.routes[i].segments)for(const b of r.routes[j].segments)shared+=sharedLength(a,b);assert.equal(shared,0);
});
test('routing is deterministic and crowded routes are explicit, never forced through chips',()=>{
 const a=rect('a',20,20),b=rect('b',300,20),wall=rect('wall',10,10,130,80),edges=[connection(a,b,'ab'),connection(b,a,'ba')];
 assert.deepEqual(routeConnections(edges,[a,b]),routeConnections([...edges].reverse(),[a,b]));const trapped=routeConnections([edges[0]],[a,b,wall]);assert.deepEqual(trapped.omitted,['ab']);assert.equal(trapped.routes.length,0);
});
test('area and neighborhood selection includes direct real neighbors without transitive flooding',()=>{
 const records=[{id:'a',area:'documentation',cluster:'c1'},{id:'b',area:'documentation',cluster:'c1'},{id:'c',area:'plans',cluster:'c2'},{id:'d',area:'evidence',cluster:'c3'},{id:'isolated',area:'plans',cluster:'c4'}],edges=[{source:'a',target:'c'},{source:'c',target:'d'}];
 for(const selection of [{kind:'area',id:'documentation'},{kind:'cluster',id:'c1'}]){const s=selectionScope({records},edges,selection);assert.deepEqual([...s.seeds],['a','b']);assert.deepEqual([...s.related],['a','b','c']);assert(!s.related.has('d'));assert(!s.related.has('isolated'));assert.deepEqual([...s.areas],['documentation','plans']);}
 const single=selectionScope({records},edges,{kind:'record',id:'c'});assert.deepEqual([...single.related].sort(),['a','c','d']);assert(!selectionScope({records},edges,null).active);
});
