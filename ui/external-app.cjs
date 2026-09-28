'use strict';
const { app, BrowserWindow, dialog, ipcMain, protocol, session } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { ExternalClient, physicalDirectory, physicalFile, separate, validRequest } = require('./external-client.cjs');
const { parseDocument } = require('./markdown-model.cjs');

const PAGE = 'cc-external://app/external.html';
const CSP = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'";
const option = name => process.argv.find(arg => arg.startsWith(`--${name}=`))?.slice(name.length + 3);
protocol.registerSchemesAsPrivileged([{ scheme: 'cc-external', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);

function existingDirectory(value, code) {
  const resolved = physicalDirectory(value);
  if (!fs.existsSync(resolved) || !fs.lstatSync(resolved).isDirectory()) throw Error(code);
  return resolved;
}
function descriptorSource(workspace, protectedRoots) {
  const descriptor = path.join(workspace, 'external.json');
  if (!fs.existsSync(descriptor)) return null;
  const info = fs.lstatSync(descriptor);
  if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.size > 16384) throw Error('invalid_descriptor');
  let value;
  try { value = JSON.parse(fs.readFileSync(descriptor, 'utf8')); } catch { throw Error('invalid_descriptor'); }
  if (!value || typeof value !== 'object' || typeof value.source !== 'string') throw Error('invalid_descriptor');
  const source = existingDirectory(value.source, 'source_missing');
  separate(...protectedRoots, source);
  return source;
}
function samePath(left, right) { return process.platform === 'win32' ? left?.toLowerCase() === right?.toLowerCase() : left === right; }
function initializedWorkspace(workspace) { return Boolean(workspace && fs.existsSync(path.join(workspace, 'external.json'))); }
function startupOptions() {
  const profile = physicalDirectory(option('profile'));
  const core = existingDirectory(option('core'), 'core_missing');
  const python = physicalFile(option('python'));
  const appRoot = existingDirectory(__dirname, 'app_missing');
  const sourceArg = option('source'), workspaceArg = option('workspace');
  let source = sourceArg ? existingDirectory(sourceArg, 'source_missing') : null;
  const workspace = workspaceArg ? existingDirectory(workspaceArg, 'workspace_missing') : null;
  if (workspace) {
    const boundSource = descriptorSource(workspace, [profile, core, appRoot, path.dirname(python), workspace]);
    if (boundSource && source && !samePath(boundSource, source)) throw Error('workspace_source_mismatch');
    if (boundSource) source = boundSource;
  }
  const dirs = [profile, core, appRoot, path.dirname(python)];
  if (source) dirs.push(source); if (workspace) dirs.push(workspace);
  separate(...dirs);
  return { profile, core, python, appRoot, source, workspace };
}
function safeSender(event, window) { return event.sender === window?.webContents && event.senderFrame?.url === PAGE; }
function safeMarkdown(value) {
  if (typeof value !== 'string' || value.length > 262144) return null;
  try { return parseDocument(value); } catch { return null; }
}
function decorate(action, result) {
  if (!result || typeof result !== 'object') return result;
  if (action === 'document' && typeof result.markdown === 'string') return { ...result, model: safeMarkdown(result.markdown) };
  if (action === 'page' && typeof result.body === 'string') return { ...result, model: safeMarkdown(result.body) };
  if (action === 'conversation-read') return {path: 'Conversation: ' + result.title,
    model: safeMarkdown('# ' + result.title + '\n\n' + result.summary + '\n\n' + (result.turns || []).map(turn => '## ' + turn.role + '\n\n' + turn.content).join('\n\n'))};
  return result;
}
async function start() {
  // All supplied roots are checked before Electron makes its user-data/session directories.
  const config = startupOptions();
  app.setName('ControlCoding External');
  app.setPath('userData', config.profile);
  app.setPath('sessionData', path.join(config.profile, 'browser'));
  app.commandLine.appendSwitch('disable-background-networking');
  app.commandLine.appendSwitch('disable-component-update');
  app.enableSandbox();
  await app.whenReady();
  let window = null, client = null, timer = null;
  const state = { mode: 'external', source: config.source, workspace: config.workspace, status: null, catalog: null, records: null, query: null, document: null, page: null, error: null, busy: false, generation: 0 };
  const snapshot = () => ({ ...state, limitations: ['No source writes or hooks', 'No build, test, apply, agents, OCR, embeddings, or network actions', 'Source changes require explicit refresh before evidence is current'] });
  const publish = () => { if (window && !window.isDestroyed()) window.webContents.send('external:state', snapshot()); };
  const clearContent = () => { state.status = null; state.catalog = null; state.records = null; state.query = null; state.document = null; state.page = null; };
  const invalidateEvidence = error => { state.catalog = null; state.records = null; state.query = null; state.document = null; state.page = null; state.status = { ...(state.status || { mode: 'external', source: state.source, workspace: state.workspace }), fresh: false, error }; };
  const protectedRoots = workspace => [config.profile, config.core, config.appRoot, path.dirname(config.python), workspace];
  const validateBinding = (action, value) => {
    if (!state.workspace) throw Error('workspace_required');
    if (action === 'init') {
      const source = existingDirectory(state.source, 'source_missing');
      separate(...protectedRoots(state.workspace), source);
      if (!value || !samePath(value.source, source)) throw Error('source_binding_required');
      return;
    }
    const bound = descriptorSource(state.workspace, protectedRoots(state.workspace));
    if (!bound || !state.source || !samePath(bound, state.source)) throw Error('workspace_source_mismatch');
  };
  const buildClient = () => {
    if (!state.workspace) return null;
    client?.cancel();
    client = new ExternalClient({ python: config.python, core: config.core, profile: config.profile, workspace: state.workspace });
    return client;
  };
  const run = async (action, value, automatic = false) => {
    if (!validRequest(action, value)) throw Error('invalid_request');
    try { validateBinding(action, value); }
    catch (error) { state.error = error.message; invalidateEvidence(error.message); publish(); return snapshot(); }
    const active = client || buildClient();
    if (state.busy) return snapshot();
    const generation = state.generation, workspace = state.workspace, source = state.source;
    state.busy = true; if (!automatic) state.error = null; publish();
    const answer = await active.run(action, value);
    if (generation !== state.generation || !samePath(workspace, state.workspace) || !samePath(source, state.source)) return snapshot();
    state.busy = false;
    if (!answer.ok) { state.error = answer.error; invalidateEvidence(answer.error); publish(); return snapshot(); }
    const result = decorate(action, answer.result);
    if (action === 'status' || action === 'refresh' || action === 'init') {
      state.status = result;
      if (result?.fresh !== true) { state.catalog = null; state.records = null; state.query = null; state.document = null; state.page = null; }
    }
    if (action === 'catalog') state.catalog = result;
    if (action === 'records') state.records = result;
    if (action === 'query') state.query = result;
    if (action === 'document') { state.document = result; state.page = null; }
    if (action === 'conversation-read') { state.document = result; state.page = null; }
    if (action === 'page') { state.page = result; state.document = null; }
    state.generation++; publish();
    if (action === 'record' || action === 'conversation') {
      await run('status', null); await run('catalog', null); await run('records', null);
    }
    return snapshot();
  };
  protocol.handle('cc-external', request => {
    const url = new URL(request.url), types = { '/external.html': 'text/html', '/external.js': 'text/javascript', '/external.css': 'text/css' };
    if (url.host !== 'app' || !Object.hasOwn(types, url.pathname) || url.search || request.method !== 'GET') return new Response('', { status: 404 });
    return new Response(fs.readFileSync(path.join(__dirname, url.pathname.slice(1))), { headers: { 'Content-Type': types[url.pathname], 'Content-Security-Policy': CSP, 'X-Content-Type-Options': 'nosniff' } });
  });
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  session.defaultSession.on('will-download', event => event.preventDefault());
  session.defaultSession.webRequest.onBeforeRequest((details, callback) => callback({ cancel: !details.url.startsWith('cc-external://app/') && !details.url.startsWith('devtools://') }));
  window = new BrowserWindow({ width: 1260, height: 820, minWidth: 860, minHeight: 620, title: 'ControlCoding External', backgroundColor: '#111820', autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, 'external-preload.cjs'), sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true, devTools: false, spellcheck: false } });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', event => event.preventDefault());
  window.webContents.on('will-attach-webview', event => event.preventDefault());
  window.on('closed', () => { client?.cancel(); clearInterval(timer); window = null; });
  const handle = (name, callback) => ipcMain.handle(`external:${name}`, async (event, ...args) => { if (!safeSender(event, window)) throw Error('untrusted_sender'); return callback(...args); });
  handle('snapshot', (...args) => { if (args.length) throw Error('unexpected_arguments'); return snapshot(); });
  handle('choose-source', async (...args) => {
    if (args.length) throw Error('unexpected_arguments');
    if (state.busy) throw Error('helper_busy'); const generation = state.generation;
    const chosen = await dialog.showOpenDialog(window, { title: 'Choose original source folder', properties: ['openDirectory','dontAddToRecent'] });
    if (chosen.canceled || !chosen.filePaths?.[0]) return snapshot();
    if (state.busy || generation !== state.generation) throw Error('selection_changed');
    const source = existingDirectory(chosen.filePaths[0], 'source_missing');
    const others = [config.profile, config.core, config.appRoot, path.dirname(config.python), source]; if (state.workspace) others.push(state.workspace); separate(...others);
    if (state.workspace) { const bound = descriptorSource(state.workspace, protectedRoots(state.workspace)); if (bound && !samePath(bound, source)) throw Error('workspace_source_mismatch'); }
    state.source = source; clearContent(); state.error = null; state.generation++; publish(); return snapshot();
  });
  handle('choose-workspace', async (...args) => {
    if (args.length) throw Error('unexpected_arguments');
    if (state.busy) throw Error('helper_busy'); const generation = state.generation;
    const chosen = await dialog.showOpenDialog(window, { title: 'Choose external workspace folder', properties: ['openDirectory','dontAddToRecent'] });
    if (chosen.canceled || !chosen.filePaths?.[0]) return snapshot();
    if (state.busy || generation !== state.generation) throw Error('selection_changed');
    const workspace = existingDirectory(chosen.filePaths[0], 'workspace_missing');
    const others = [config.profile, config.core, config.appRoot, path.dirname(config.python), workspace]; if (state.source) others.push(state.source); separate(...others);
    const boundSource = descriptorSource(workspace, [config.profile, config.core, config.appRoot, path.dirname(config.python), workspace]);
    if (boundSource && state.source && !samePath(boundSource, state.source)) throw Error('workspace_source_mismatch');
    state.workspace = workspace; if (boundSource) state.source = boundSource; buildClient(); clearContent(); state.error = null; state.generation++; publish(); return snapshot();
  });
  handle('request', async (...args) => { if (args.length !== 2 || !validRequest(args[0], args[1])) throw Error('invalid_request'); return run(args[0], args[1]); });
  await window.loadURL(PAGE);
  if (state.workspace) { buildClient(); if (initializedWorkspace(state.workspace)) run('status', null, true).catch(() => {}); }
  timer = setInterval(() => { if (state.workspace && initializedWorkspace(state.workspace) && !state.busy) run('status', null, true).catch(() => {}); }, 15000);
  return { window, client: () => client, snapshot, state, show: () => { if (window && !window.isDestroyed()) { window.show(); window.focus(); } }, run };
}
module.exports = { start, startupOptions, safeMarkdown };
