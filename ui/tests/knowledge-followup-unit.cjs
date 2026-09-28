'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {PLAN_INSTRUCTION, parseQueries, mergePackets} = require('../knowledge-followup.cjs');

const citation = (name, id = 'S1') => ({id, source: name, path: `${name}.md`, title: name,
  revision: 'r1', line: 1, end_line: 2, excerpt: `${name} evidence`, kind: 'document'});
const packet = (query, names, extra = {}) => ({query, generation: 4, work_revision: 'work-1',
  observed_at: '2026-09-25T00:00:00Z', warning: null, mode: 'lexical BM25',
  answerability: 'not_assessed', citations: names.map((name, index) => citation(name, `S${index + 1}`)),
  edges: [{source: 'stale', target: 'old'}], ...extra});

test('plan instruction limits the planner to search strings and original scope', () => {
  assert.match(PLAN_INSTRUCTION, /JSON object/);
  assert.match(PLAN_INSTRUCTION, /original question/);
  assert.match(PLAN_INSTRUCTION, /do not answer|do not.*request tools/i);
});

test('strict planner JSON yields trimmed, distinct searches', () => {
  assert.deepEqual(parseQueries('{"queries":["  alpha detail  ","beta detail"]}', 'Original question'),
    ['alpha detail', 'beta detail']);
});

for (const [label, text, incomplete] of [
  ['fenced', '```json\n{"queries":["alpha"]}\n```'],
  ['prose', 'Try this: {"queries":["alpha"]}'],
  ['extra key', '{"queries":["alpha"],"tool":"shell"}'],
  ['duplicate key', '{"queries":["alpha"],"queries":["beta"]}'],
  ['malformed', '{"queries":["alpha"]'],
  ['array root', '["alpha"]'],
  ['zero queries', '{"queries":[]}'],
  ['three queries', '{"queries":["a","b","c"]}'],
  ['blank query', '{"queries":["  "]}'],
  ['nonstring query', '{"queries":[4]}'],
  ['control character', '{"queries":["a\\nb"]}'],
  ['duplicate search', '{"queries":["Alpha   Detail"," alpha detail "]}'],
  ['original question repeated', '{"queries":["  ORIGINAL  QUESTION "]}'],
  ['oversize query', JSON.stringify({queries:['a'.repeat(501)]})],
  ['incomplete output', '{"queries":["alpha"]}', true],
]) {
  test(`rejects ${label}`, () => assert.throws(() => parseQueries(text, 'Original question', incomplete)));
}

test('round-robin gives novel follow-up passages first, deduplicates, and renumbers IDs', () => {
  const original = packet('Original question', ['old1', 'old2'], {extra: {retained: true}, warning: 'original warning'});
  const first = packet('first search', ['old1', 'a', 'b'], {warning: 'first warning'});
  const second = packet('second search', ['a', 'c', 'd'], {warning: 'second warning'});
  const before = structuredClone([original, first, second]);
  const merged = mergePackets(original, [first, second]);
  assert.deepEqual(merged.citations.map(c => c.source), ['a', 'c', 'b', 'd', 'old1', 'old2']);
  assert.deepEqual(merged.citations.map(c => c.id), ['S1', 'S2', 'S3', 'S4', 'S5', 'S6']);
  assert.equal(merged.query, 'Original question');
  assert.equal(merged.mode, 'Follow-up retrieval');
  assert.equal(merged.followup.new_passages, 4);
  assert.equal(merged.followup.exhausted, true);
  assert.match(merged.followup.notice, /not exhaustive/);
  assert.deepEqual(merged.followup.attempts, [
    {query: 'first search', returned: 3, warning: 'first warning'},
    {query: 'second search', returned: 3, warning: 'second warning'},
  ]);
  assert.deepEqual(merged.warnings, ['original warning', 'first warning', 'second warning']);
  assert.deepEqual(merged.edges, []);
  assert.equal(merged.answerability, 'not_assessed');
  assert.match(merged.answer, /whether they answer the question has not been assessed/);
  assert.deepEqual([original, first, second], before);
  assert.notStrictEqual(merged.citations[0], first.citations[1]);
});

test('citation identity uses passage fields, not incoming citation IDs', () => {
  const original = packet('question', ['old']);
  const samePassage = {...citation('old', 'S99')};
  const changedExcerpt = {...citation('old', 'S1'), excerpt: 'A different passage'};
  const changedLine = {...citation('old', 'S1'), line: 3};
  const merged = mergePackets(original, [packet('search', [], {
    citations: [samePassage, changedExcerpt, changedLine], warnings: ['same warning', 'same warning'],
  })]);
  assert.deepEqual(merged.citations.map(c => c.excerpt), ['A different passage', 'old evidence', 'old evidence']);
  assert.deepEqual(merged.citations.map(c => c.line), [1, 3, 1]);
  assert.equal(merged.followup.new_passages, 2);
  assert.deepEqual(merged.warnings, ['same warning']);
});

test('result and original citation limits hold under cap, including repeated IDs', () => {
  const original = packet('question', Array.from({length: 10}, (_, i) => `old${i}`));
  const first = packet('first', Array.from({length: 10}, (_, i) => `a${i}`));
  const second = packet('second', Array.from({length: 10}, (_, i) => `b${i}`));
  const merged = mergePackets(original, [first, second]);
  assert.equal(merged.citations.length, 10);
  assert.deepEqual(merged.citations.map(c => c.source), ['a0','b0','a1','b1','a2','b2','a3','b3','a4','b4']);
  assert.equal(merged.followup.new_passages, 10);
  assert.throws(() => mergePackets({...original, citations: [...original.citations, citation('too-many')]}, []));
  assert.throws(() => mergePackets(original, [{...first, citations: [...first.citations, citation('too-many')]}]));
  assert.throws(() => mergePackets(original, [first, second, first]));
});

test('mixed generation and work revision cannot merge', () => {
  const original = packet('question', ['old']);
  assert.throws(() => mergePackets(original, [packet('later', ['new'], {generation: 5})]));
  assert.throws(() => mergePackets(original, [packet('later', ['new'], {work_revision: 'work-2'})]));
  const withoutRevision = {...original, work_revision: undefined};
  assert.equal(mergePackets(withoutRevision, [packet('later', ['new'], {work_revision: undefined})]).citations.length, 2);
});

test('empty follow-up results retain original evidence; no passages stay neutral', () => {
  const original = packet('question', ['old'], {warning: 'original warning'});
  const merged = mergePackets(original, []);
  assert.deepEqual(merged.citations.map(c => c.source), ['old']);
  assert.equal(merged.followup.new_passages, 0);
  assert.deepEqual(merged.followup.attempts, []);
  const empty = mergePackets(packet('question', []), [packet('search', [], {warning: 'no hit'})]);
  assert.deepEqual(empty.citations, []);
  assert.equal(empty.retrieval_status, 'no_matches');
  assert.equal(empty.abstained, true);
  assert.match(empty.answer, /whether an answer exists has not been assessed/);
  assert.equal(empty.followup.attempts[0].returned, 0);
});
