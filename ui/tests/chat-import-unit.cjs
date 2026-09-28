'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {parseChat}=require('../chat-import.cjs');
test('Markdown import retains visible turns, ignores fenced role markers and deduplicates line endings',async()=>{
 const raw='# User\nHello\n## Assistant\nAnswer\n```\nUser: example\n```';
 const a=await parseChat(raw,'markdown','A'),b=await parseChat(raw.replaceAll('\n','\r\n'),'markdown','B');
 assert.equal(a.id,b.id);assert.deepEqual(a.turns,b.turns);assert.equal(a.turns.length,2);assert(a.turns[1].content.includes('User: example'));
 assert.equal(a.turns[1].provenance.origin,'external-import:declared-speaker');
});
test('unlabelled text is explicitly uncertain and long turns split without losing content',async()=>{
 const a=await parseChat('Private notes','text');assert(a.turns[0].content.includes('speaker unverified'));assert.equal(a.retention,'transcript');
 const b=await parseChat('# User\n'+'x'.repeat(17000),'markdown');assert.equal(b.turns.length,2);assert.equal(b.turns.map(t=>t.content).join(''),'x'.repeat(17000));
});
test('invalid and oversized imports are rejected before preview',async()=>{
 await assert.rejects(parseChat('','text'));await assert.rejects(parseChat('x'.repeat(59001),'text'));await assert.rejects(parseChat('{}','json'));
});
