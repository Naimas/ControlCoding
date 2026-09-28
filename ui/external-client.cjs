'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');

const ACTIONS = Object.freeze(['init','status','refresh','catalog','records','query','document','page','record','conversation','conversation-read']);
const RECORD_KINDS = new Set(['objective','phase','task','blocker','decision','evidence','note']);
const MAX_INPUT = 128 * 1024;
const MAX_OUTPUT = 4 * 1024 * 1024;
const WINDOWS_BAD = /[:<>"|?*]/;

function ordinaryPath(value, kind) {
  if (typeof value !== 'string' || !value || value.length > 4096 || value.includes('\0') || !path.isAbsolute(value)) throw Error('absolute_path_required');
  if (process.platform === 'win32' && (!/^[A-Za-z]:[\\/]/.test(value) || value.replaceAll('/', '\\').startsWith('\\'))) throw Error('special_path_unsupported');
  const resolved = path.resolve(value), root = path.parse(resolved).root;
  const parts = resolved.slice(root.length).split(path.sep).filter(Boolean);
  if (parts.length > 128) throw Error('path_depth_limit');
  let current = root;
  for (const part of parts) {
    if (process.platform === 'win32' && (/[ .]$/.test(part) || WINDOWS_BAD.test(part))) throw Error('ambiguous_path');
    current = path.join(current, part);
    try {
      const info = fs.lstatSync(current);
      if (info.isSymbolicLink() || (current !== resolved && !info.isDirectory())) throw Error('linked_or_special_path');
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  if (kind === 'file') {
    // Canonicalize the containing directory, then inspect the leaf as a file.
    // Treating the leaf as the ancestor would incorrectly require it to be a directory.
    const parent = ordinaryPath(path.dirname(resolved), 'directory');
    const file = path.join(parent, path.basename(resolved));
    let info;
    try { info = fs.lstatSync(file); } catch { throw Error('file_missing'); }
    if (!info.isFile() || info.isSymbolicLink() || info.nlink > 1) throw Error('ordinary_file_required');
    return file;
  }
  let ancestor = resolved, suffix = [];
  while (!fs.existsSync(ancestor)) {
    if (path.dirname(ancestor) === ancestor) throw Error('path_root_missing');
    suffix.unshift(path.basename(ancestor)); ancestor = path.dirname(ancestor);
  }
  const info = fs.lstatSync(ancestor);
  if (info.isSymbolicLink() || !info.isDirectory()) throw Error('linked_or_special_path');
  const physical = path.join(fs.realpathSync.native(ancestor), ...suffix);
  return physical;
}
function physicalDirectory(value) { return ordinaryPath(value, 'directory'); }
function physicalFile(value) { return ordinaryPath(value, 'file'); }
function overlaps(left, right) {
  if (process.platform === 'win32') { left = left.toLowerCase(); right = right.toLowerCase(); }
  const contains = (a, b) => { const relative = path.relative(a, b); return relative === '' || (!path.isAbsolute(relative) && relative !== '..' && !relative.startsWith('..' + path.sep)); };
  return contains(left, right) || contains(right, left);
}
function separate(...paths) {
  for (let left = 0; left < paths.length; left++) for (let right = left + 1; right < paths.length; right++) if (overlaps(paths[left], paths[right])) throw Error('overlapping_paths');
  return paths;
}
function text(value, maximum = 4096) { return typeof value === 'string' && value.length <= maximum && !/[\0\x01-\x08\x0b\x0c\x0e-\x1f]/.test(value); }
function relative(value) { return text(value, 1024) && value !== '' && !path.isAbsolute(value) && !value.split(/[\\/]/).includes('..') && !value.split(/[\\/]/).includes(''); }
function record(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || !RECORD_KINDS.has(value.kind) || !text(value.title, 180) || !text(value.body, 8000) ||
    !Array.isArray(value.sources) || value.sources.length > 64 || !Array.isArray(value.links) || value.links.length > 64 || !value.links.every(id => text(id, 160))) return false;
  if (value.id !== undefined && !text(value.id, 160)) return false;
  return value.sources.every(source => source && Object.keys(source).sort().join(',') === 'path,sha256' && relative(source.path) && /^[a-f0-9]{64}$/.test(source.sha256));
}
function conversation(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || !text(value.id, 160) || !text(value.title, 180) || value.retention !== 'transcript' || value.status !== 'closed' || !text(value.summary, 8192) || !Array.isArray(value.turns) || value.turns.length > 512) return false;
  return value.turns.every(turn => turn && text(turn.id, 160) && Number.isInteger(turn.sequence) && turn.sequence >= 0 && turn.sequence < 1000000 && ['user','assistant'].includes(turn.role) && text(turn.content, 16000) && turn.provenance && Object.keys(turn.provenance).length === 1 && turn.provenance.origin === 'manual');
}
function validRequest(action, value) {
  if (!ACTIONS.includes(action)) return false;
  if (['status','refresh','catalog','records'].includes(action)) return value === null;
  if (action === 'init') return value && Object.keys(value).sort().join(',') === 'include,source' && typeof value.source === 'string' && path.isAbsolute(value.source) && Array.isArray(value.include) && value.include.length > 0 && value.include.length <= 512 && value.include.every(relative);
  if (action === 'query') return text(value, 500) && value.trim().length > 0;
  if (action === 'document') return relative(value);
  if (action === 'page') return text(value, 160) && value.trim().length > 0;
  if (action === 'conversation-read') return typeof value === 'string' && /^[A-Za-z0-9_-]{1,80}$/.test(value);
  if (action === 'record') return record(value);
  return conversation(value);
}
function externalEnvironment(profile) {
  const env = { PYTHONDONTWRITEBYTECODE: '1', PYTHONIOENCODING: 'utf-8', TEMP: profile, TMP: profile };
  for (const name of ['SystemRoot', 'WINDIR', 'COMSPEC']) if (process.env[name]) env[name] = process.env[name];
  if (process.platform !== 'win32' && process.env.LANG) env.LANG = process.env.LANG;
  return env;
}
class ExternalClient {
  constructor({ python, core, profile, workspace, spawnImpl = spawn, timeout = 150000 }) {
    this.python = physicalFile(python); this.core = physicalDirectory(core); this.profile = physicalDirectory(profile);
    this.workspace = physicalDirectory(workspace); this.script = physicalFile(path.join(this.core, 'scripts', 'cc_external.py'));
    separate(this.core, this.profile, this.workspace, path.dirname(this.python));
    this.spawn = spawnImpl; this.timeout = timeout; this.active = null;
  }
  cancel() { if (this.active) this.active('cancelled'); }
  run(action, value) {
    if (!validRequest(action, value)) return Promise.resolve({ ok: false, error: 'invalid_request' });
    if (this.active) return Promise.resolve({ ok: false, error: 'helper_busy' });
    const request = { action, workspace: this.workspace, value };
    const input = Buffer.from(JSON.stringify(request), 'utf8');
    if (input.length > MAX_INPUT) return Promise.resolve({ ok: false, error: 'input_limit' });
    return new Promise(resolve => {
      let child, timer, done = false, size = 0, stderr = 0, chunks = [];
      const finish = (error, response) => {
        if (done) return; done = true; clearTimeout(timer); if (this.active === cancel) this.active = null;
        if (error && child?.exitCode === null) child.kill(); resolve(response || { ok: false, error });
      };
      const cancel = error => finish(error);
      this.active = cancel;
      // Init is deliberately outside the workspace so no cwd-owned incidental
      // file can precede its descriptor; existing operations run at workspace.
      const cwd = action === 'init' ? this.profile : this.workspace;
      try { child = this.spawn(this.python, ['-I', '-B', this.script, '--stdio'], { cwd, env: externalEnvironment(this.profile), windowsHide: true, shell: false, stdio: ['pipe','pipe','pipe'] }); }
      catch { finish('helper_unavailable'); return; }
      timer = setTimeout(() => finish('helper_timeout'), this.timeout);
      child.on('error', () => finish('helper_unavailable'));
      child.stdin.on('error', () => finish('helper_failure'));
      child.stderr.on('data', chunk => { stderr += chunk.length; if (stderr > 16384) finish('helper_failure'); });
      child.stdout.on('data', chunk => { size += chunk.length; if (size > MAX_OUTPUT) return finish('helper_output_limit'); chunks.push(chunk); });
      child.on('close', code => {
        if (done) return;
        if (stderr) return finish('helper_failure');
        try {
          const response = JSON.parse(Buffer.concat(chunks).toString('utf8'));
          if (!response || typeof response !== 'object' || Array.isArray(response) || typeof response.ok !== 'boolean' ||
            (response.ok && !Object.hasOwn(response, 'result')) || (!response.ok && typeof response.error !== 'string')) throw Error();
          // Expected service rejections intentionally use a nonzero exit code.
          if (code !== 0 && response.ok) throw Error();
          finish(null, response);
        } catch { finish('invalid_helper_response'); }
      });
      child.stdin.end(input);
    });
  }
}
module.exports = { ACTIONS, MAX_INPUT, MAX_OUTPUT, ExternalClient, physicalDirectory, physicalFile, overlaps, separate, validRequest, externalEnvironment };
