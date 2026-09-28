'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { EventEmitter } = require('node:events');
const { validRequest, physicalDirectory, physicalFile, separate, externalEnvironment, ExternalClient } = require('../external-client.cjs');

test('external action allowlist accepts only the bounded request shapes', () => {
  assert(validRequest('status', null)); assert(validRequest('refresh', null)); assert(validRequest('catalog', null)); assert(validRequest('records', null));
  assert(validRequest('init', { source: 'C:/source', include: ['README.md', 'docs/guide.md'] }));
  assert(validRequest('query', 'What is the release boundary?')); assert(validRequest('document', 'docs/guide.md')); assert(validRequest('page', 'wiki-page'));
  assert(validRequest('record', { kind: 'evidence', title: 'Evidence', body: 'Bound to source bytes.', sources: [{ path: 'README.md', sha256: 'a'.repeat(64) }], links: [] }));
  assert(validRequest('conversation', { id: 'manual-1', title: 'Manual note', retention: 'transcript', summary: 'Note', status: 'closed', turns: [{ id: 'turn-1', sequence: 0, role: 'user', content: 'Kept manually.', provenance: { origin: 'manual' } }] }));
  for (const request of [['exec', null], ['knowledge', null], ['panel:read', null], ['status', {}], ['init', { source: 'C:/source', include: ['../secret.md'] }], ['document', 'C:/source/README.md'], ['query', 'x'.repeat(501)], ['record', { kind: 'run', title: 'x', body: 'y', sources: [], links: [] }], ['conversation', { id: 'x', title: 'x', retention: 'none', summary: 'x', status: 'closed', turns: [] }]]) assert(!validRequest(...request));
});
test('path guards preserve ordinary external directory rules', () => {
  assert.throws(() => physicalDirectory('relative'), /absolute_path_required/);
  assert.equal(physicalFile(__filename), fs.realpathSync.native(__filename));
  assert.equal(typeof physicalDirectory(path.parse(process.cwd()).root), 'string');
  assert.throws(() => separate('C:/same', 'C:/same/child'), /overlapping_paths/);
  assert.doesNotThrow(() => separate('C:/external-source', 'C:/external-workspace', 'C:/external-profile'));
});
test('external client uses only the stdio transport and a bounded isolated request', { skip: !process.env.CC_EXTERNAL_TEST_ROOT }, async () => {
  const root = process.env.CC_EXTERNAL_TEST_ROOT;
  const profile = path.join(root, 'profile'), workspace = path.join(root, 'workspace'), core = path.join(root, 'core');
  fs.mkdirSync(path.join(core, 'scripts'), { recursive: true }); fs.mkdirSync(profile, { recursive: true }); fs.mkdirSync(workspace, { recursive: true });
  const script = path.join(core, 'scripts', 'cc_external.py'); fs.writeFileSync(script, '# test transport fixture\n', { flag: 'w' });
  let launched, encoded;
  const spawnImpl = (binary, args, options) => {
    launched = { binary, args, options }; const child = new EventEmitter(); child.exitCode = null; child.kill = () => { child.exitCode = 1; };
    child.stdout = new EventEmitter(); child.stderr = new EventEmitter(); child.stdin = new EventEmitter();
    child.stdin.end = value => { encoded = JSON.parse(Buffer.from(value).toString('utf8')); queueMicrotask(() => { child.stdout.emit('data', Buffer.from(JSON.stringify({ ok: true, result: { answer: 'bounded' } }))); child.exitCode = 0; child.emit('close', 0); }); };
    return child;
  };
  const client = new ExternalClient({ python: process.execPath, core, profile, workspace, spawnImpl });
  assert.deepEqual(await client.run('query', 'bounded?'), { ok: true, result: { answer: 'bounded' } });
  assert.equal(launched.binary, fs.realpathSync.native(process.execPath)); assert.deepEqual(launched.args, ['-I', '-B', fs.realpathSync.native(script), '--stdio']);
  assert.equal(launched.options.cwd, fs.realpathSync.native(workspace)); assert.equal(launched.options.shell, false); assert.equal(launched.options.windowsHide, true); assert.equal(launched.options.env.PATH, undefined);
  assert.deepEqual(encoded, { action: 'query', workspace: fs.realpathSync.native(workspace), value: 'bounded?' });
});
test('external child environment omits host Python, pip, git, and path options', () => {
  const environment = externalEnvironment('C:/external-profile');
  for (const key of Object.keys(environment)) assert(!/^(PYTHON|PIP|GIT|PATH)/.test(key) || ['PYTHONDONTWRITEBYTECODE','PYTHONIOENCODING'].includes(key));
  assert.equal(environment.TEMP, 'C:/external-profile'); assert.equal(environment.TMP, 'C:/external-profile');
});
