'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {organizeKnowledge,topicFor}=require('../knowledge-topics.cjs'),{layout}=require('../knowledge-layout.cjs');
const doc=(id,extra={})=>({id,title:id,path:'docs/'+id+'.md',area:'documentation',state:'Observed source',origin:'Repository',hash:'a'.repeat(64),excerpt:'',...extra});
const meta=(series,revision)=>`---\ncc-topic: Development plans\ncc-series: ${series}\ncc-revision: ${revision}\n---\n# Plan`;
test('topics are named and explainable, with declared and per-view assignments taking precedence',()=>{
 assert.equal(topicFor(doc('roadmap')).topic,'Development plans');assert.equal(topicFor(doc('project-map-panel')).topic,'Project Map');assert.equal(topicFor(doc('memory-schema')).topic,'Memory & GraphRAG');
 assert.equal(topicFor(doc('one',{excerpt:meta('a',1)})).topic,'Development plans');assert.equal(topicFor(doc('one',{excerpt:meta('a',1)}),'Authentication').topic,'Authentication');
 assert.equal(topicFor(doc('one',{category:'Deployment'})).topic,'Deployment');assert(!topicFor(doc('one',{excerpt:'---\ncc-topic: Truncated'})).topic.includes('Truncated'));
});
test('declared revision series displays only latest and retains ordered original IDs',()=>{
 const nodes=[doc('first',{excerpt:meta('Plan',1)}),doc('third',{excerpt:meta('Plan',3)}),doc('second',{excerpt:meta('Plan',2)}),doc('other')],edges=[{source:'first',target:'other',kind:'Markdown reference'}],m=organizeKnowledge(nodes,edges);
 assert.deepEqual(m.nodes.map(n=>n.id),['third','other']);assert.deepEqual(m.families.third,['third','second','first']);assert.equal(m.currentById.first,'third');assert.equal(m.nodes[0].historyCount,2);assert.equal(m.edges.length,0,'historical edges are not reassigned to the latest file');assert.equal(m.hiddenCount,2);
});
test('recorded replacement edges and declared predecessor paths produce the same linear history',()=>{
 const a=doc('a'),b=doc('b'),c=doc('c',{excerpt:'---\ncc-supersedes: docs/b.md\n---\n# C'}),m=organizeKnowledge([a,b,c],[{source:'a',target:'b',kind:'Recorded archive relation: superseded_by'}]);
 assert.deepEqual(m.families.c,['c','b','a']);assert.equal(m.nodes.length,1);
});
test('cycles, branches, duplicate revisions and missing predecessors never hide ambiguous sources',()=>{
 const a=doc('a'),b=doc('b'),c=doc('c');for(const edges of [[{source:'a',target:'b',kind:'supersedes'},{source:'b',target:'a',kind:'supersedes'}],[{source:'a',target:'b',kind:'supersedes'},{source:'c',target:'b',kind:'supersedes'}]]){const m=organizeKnowledge([a,b,c],edges);assert.equal(m.hiddenCount,0);assert(m.issues.length);}
 const duplicate=organizeKnowledge([doc('a',{excerpt:meta('Same',1)}),doc('b',{excerpt:meta('Same',1)})],[]);assert.equal(duplicate.hiddenCount,0);assert(duplicate.issues.length);
 const missing=organizeKnowledge([doc('a',{excerpt:'---\ncc-supersedes: docs/missing.md\n---'})],[]);assert.equal(missing.hiddenCount,0);assert(missing.issues.length);
});
test('numbered filenames require confirmation; dates and title similarity do not imply replacement',()=>{
 const nodes=[doc('design-v1'),doc('design-v2'),doc('design-20260924'),doc('design-20260925')],first=organizeKnowledge(nodes,[]);assert.equal(first.hiddenCount,0);assert.equal(first.candidates.length,1);
 const confirmed=organizeKnowledge(nodes,[],{},[first.candidates[0].id]);assert.equal(confirmed.hiddenCount,1);assert.deepEqual(confirmed.families['design-v2'],['design-v2','design-v1']);
 const changed=organizeKnowledge(nodes.map(n=>n.id==='design-v1'?{...n,hash:'b'.repeat(64)}:n),[],{},[first.candidates[0].id]);assert.equal(changed.hiddenCount,0,'confirmation is tied to observed identities');
});
test('radial topic hubs and all document rectangles remain disjoint across dense rings',()=>{
 const overlaps=(a,b)=>a.x<b.x+b.w-.001&&a.x+a.w>b.x+.001&&a.y<b.y+b.h-.001&&a.y+a.h>b.y+.001;
 for(const count of [1,2,7,8,9,16,24,25,48,80,120,200]){const m=layout(Array.from({length:count},(_,i)=>doc('item'+i,{topic:'Development plans'})),[]);assert.equal(m.clusters.length,1);const c=m.clusters[0];assert.equal(c.title,'Development plans');assert(Math.abs(c.hub.x+c.hub.w/2-(c.x+c.w/2))<.001);for(let i=0;i<m.records.length;i++){const a=m.records[i];assert(!overlaps(a,c.hub));for(let j=i+1;j<m.records.length;j++)assert(!overlaps(a,m.records[j]),`${count}: ${i}/${j}`);}}
});

test('subject evidence is bounded and inspectable; conflicts and incidental substrings remain pending',()=>{
 for(const title of ['Planet notes','Panelist biography','Agentic branding','Unrelated notes']){
  const m=topicFor(doc('neutral',{title,path:'docs/random-folder/note.md'}));assert.equal(m.topic,'Needs classification');assert(m.topicReason.includes('No explicit topic'));
 }
 const ambiguous=topicFor(doc('neutral',{title:'Memory and agents',path:'docs/note.md'}));assert.equal(ambiguous.topic,'Needs classification');assert(ambiguous.topicReason.includes('Conflicting subject evidence'));
 const focused=topicFor(doc('neutral',{title:'Memory system schema',path:'docs/note.md'}));assert.equal(focused.topic,'Memory & GraphRAG');assert(focused.topicReason.includes('title contains "Memory"'));
 assert(topicFor(doc('one',{excerpt:meta('a',1)})).topicReason.includes('cc-topic: Development plans'));
});

test('current heading evidence wins over a historical roadmap filename',()=>{
 const reference=topicFor(doc('ref',{title:'Trust and Verification Reference',path:'docs/roadmap-10-10.md'}));assert.equal(reference.topic,'Verification & quality');assert(reference.topicReason.includes('title contains "Verification"'));
 const actual=topicFor(doc('plan',{title:'Documentation Maintenance Plan',path:'docs/docs-maintenance-plan.md'}));assert.equal(actual.topic,'Development plans');
 const fallback=topicFor(doc('fallback',{title:'Overview',path:'docs/development-plan.md'}));assert.equal(fallback.topic,'Development plans');assert(fallback.topicReason.includes('filename'));
});

test('historical plan filenames do not retain references in the Plans macro area',()=>{
 const input=[doc('reference',{title:'Trust and Verification Reference',path:'docs/roadmap-10-10.md',area:'plans'}),doc('maintenance',{title:'Documentation Maintenance',path:'docs/docs-maintenance-plan.md',area:'plans'}),doc('actual',{title:'Local observer roadmap',path:'ROADMAP.md',area:'plans'})];
 const m=organizeKnowledge(input,[]);assert.deepEqual(m.nodes.map(n=>n.area),['evidence','documentation','plans']);assert.deepEqual(m.nodes.filter(n=>n.topic==='Development plans').map(n=>n.id),['actual']);assert(input.every(n=>n.area==='plans'),'observed records are not mutated');
});
