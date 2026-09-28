'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {validateAnswer}=require('../knowledge-answer.cjs');
const citations=[{id:'S1'},{id:'S2'}];
test('exact abstention is accepted without fabricated citations',()=>{
 const value=validateAnswer(' \nINSUFFICIENT_EVIDENCE\n',citations,false);
 assert.equal(value.abstained,true);assert.equal(value.status,'insufficient_evidence');
 assert(!value.text.includes('[S1]'));
});
test('cited prose remains an unverified draft',()=>{
 const value=validateAnswer('A documented decision [S1].',citations,false);
 assert.equal(value.status,'cited_draft');assert.equal(value.abstained,false);
});
for(const text of ['INSUFFICIENT_EVIDENCE but definitely true [S1]','[S1] INSUFFICIENT_EVIDENCE',
 'A made up answer [S99].','Uncited assertion.','']){
 test('invalid response rejected: '+text,()=>assert.throws(()=>validateAnswer(text,citations,false)));
}
for(const text of ['INSUFFICIENT_EVIDENCE','Supported [S1].']){
 test('truncated output rejected: '+text,()=>assert.throws(()=>validateAnswer(text,citations,true)));
}
