'use strict';
const { app, BrowserWindow, ipcMain, dialog, Tray, Menu, nativeImage, globalShortcut, screen, protocol, session, clipboard } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { BridgeClient } = require('./bridge-client.cjs');
const { TABS, allowedSender, safePreferences, visibleBounds, PanelState } = require('./panel-state.cjs');
const {separateProfile} = require('./profile-paths.cjs');
const PAGE = 'cc-panel://app/index.html';
const CSP = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'";
const option = name => process.argv.find(a => a.startsWith(`--${name}=`))?.slice(name.length + 3);
protocol.registerSchemesAsPrivileged([{ scheme: 'cc-panel', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);
app.setName('ControlCoding Panel');
app.commandLine.appendSwitch('disable-background-networking');
app.commandLine.appendSwitch('disable-component-update');
app.enableSandbox();
let window, tray, store, client, quitting = false, prefs, shortcutStatus = 'unavailable', preferenceStatus = 'available';
let profile, preferencesFile;

function loadPreferences() {
  try {
    const info = fs.lstatSync(preferencesFile);
    if (!info.isFile() || info.isSymbolicLink() || info.size > 8192) throw Error();
    return safePreferences(JSON.parse(fs.readFileSync(preferencesFile, 'utf8')));
  } catch (error) { if (error.code !== 'ENOENT') preferenceStatus = 'ignored_invalid'; return safePreferences(); }
}
function savePreferences() {
  try {
    fs.mkdirSync(profile, { recursive: true });
    const info = fs.lstatSync(profile);
    if (!info.isDirectory() || info.isSymbolicLink()) throw Error();
    const temporary = path.join(profile, `preferences-${process.pid}.tmp`);
    const fd = fs.openSync(temporary, 'wx');
    try { fs.writeFileSync(fd, JSON.stringify(prefs)); } finally { fs.closeSync(fd); }
    try { fs.renameSync(temporary, preferencesFile); }
    catch (e) { fs.unlinkSync(temporary); throw e; }
    preferenceStatus = 'available';
  } catch { preferenceStatus = 'save_failed'; }
}
function snapshot() { return { ...store.value, preferences: prefs, shortcutStatus, preferenceStatus,
  runtime: { electron: process.versions.electron, platform: process.platform }, trayAvailable: Boolean(tray) }; }
function notify() { if (window && !window.isDestroyed()) window.webContents.send('panel:state', snapshot()); }
function show() { if (window.isMinimized()) window.restore(); window.show(); window.focus(); window.webContents.focus(); }
function recall() { if (window.isVisible() && !window.isMinimized()) window.hide(); else show(); }
function registerShortcut() {
  globalShortcut.unregisterAll();
  if (prefs.shortcut === 'disabled') { shortcutStatus = 'disabled'; return; }
  try { shortcutStatus = globalShortcut.register(prefs.shortcut, recall) ? 'registered' : 'unavailable'; }
  catch { shortcutStatus = 'unavailable'; }
}
function icon() {
  // Small generated RGBA app-owned tray mark; no remote asset or downloaded icon.
  const pixels = Buffer.alloc(24 * 24 * 4);
  for (let y = 0; y < 24; y++) for (let x = 0; x < 24; x++) {
    const i = (y * 24 + x) * 4, mark = (x >= 5 && x < 19 && y >= 5 && y < 19 && (x < 8 || y < 8 || y > 15));
    pixels[i] = mark ? 255 : 36; pixels[i+1] = mark ? 159 : 38; pixels[i+2] = mark ? 82 : 42; pixels[i+3] = 255;
  }
  return nativeImage.createFromBitmap(pixels, { width: 24, height: 24 });
}
async function start() {
  // Reject overlap before Electron can create userData/sessionData at the supplied path.
  profile = separateProfile(option('profile') || path.join(app.getPath('appData'), 'ControlCoding Panel'), option('project'));
  separateProfile(profile,__dirname);
  if(option('core'))separateProfile(profile,option('core'));
  const packageRoot=path.dirname(__dirname);
  if(fs.existsSync(path.join(packageRoot,'MANIFEST.json')))separateProfile(profile,packageRoot);
  app.setPath('userData', profile);
  app.setPath('sessionData', path.join(profile, 'browser'));
  preferencesFile = path.join(profile, 'preferences.json');
  await app.whenReady();
  prefs = loadPreferences();
  const core = option('core'), python = option('python');
  if (!core || !python || !path.isAbsolute(core) || !path.isAbsolute(python)) {
    dialog.showErrorBox('ControlCoding Panel', 'Launch with absolute --core and --python paths.'); app.quit(); return;
  }
  let analysisRuntime=null;
  try {if(JSON.parse(fs.readFileSync(path.join(__dirname,'analysis-runtime.json'),'utf8')).available===true)
    analysisRuntime=[process.execPath,path.join(__dirname,'map-language-worker.cjs')];}catch{}
  client = new BridgeClient(python, path.join(core, 'scripts', 'cc_panel_bridge.py'),{analysisRuntime});
  store = new PanelState(client, notify, {}, root => separateProfile(profile,root));
  store.jobs=new (require('./panel-jobs.cjs').PanelJobs)(store,{python,core,profile});
  store.ai=new (require('./ai-session.cjs').AISession)(store);
  store.roles=new (require('./ai-roles.cjs').AIRoles)(store,{profile,copyText:text=>clipboard.writeText(text)});
  store.knowledge=new (require('./knowledge-state.cjs').KnowledgeState)(store,
    new BridgeClient(python,path.join(core,'scripts','cc_panel_bridge.py'),{timeout:150000}),{python,core,profile,archiveClient:new BridgeClient(python,path.join(core,'scripts','cc_panel_bridge.py'),{timeout:20000})});
  store.consolidation=new (require('./knowledge-consolidation.cjs').ConsolidationRunner)(store,{copyText:text=>clipboard.writeText(text),clientFactory:()=>new BridgeClient(python,path.join(core,'scripts','cc_panel_bridge.py'),{timeout:150000})});
  protocol.handle('cc-panel', request => {
    const url = new URL(request.url);
    const names = { '/index.html': 'text/html', '/app.js': 'text/javascript', '/style.css': 'text/css' };
    if (url.host !== 'app' || !Object.hasOwn(names, url.pathname) || url.search || request.method !== 'GET') return new Response('', { status: 404 });
    return new Response(fs.readFileSync(path.join(__dirname, url.pathname.slice(1))), {
      headers: { 'Content-Type': names[url.pathname], 'Content-Security-Policy': CSP, 'X-Content-Type-Options': 'nosniff' } });
  });
  session.defaultSession.setPermissionRequestHandler((_web, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  session.defaultSession.on('will-download', event => event.preventDefault());
  session.defaultSession.webRequest.onBeforeRequest((details, callback) => callback({ cancel: !details.url.startsWith('cc-panel://app/') && !details.url.startsWith('devtools://') }));
  window = new BrowserWindow({ ...visibleBounds(prefs.bounds, screen.getAllDisplays(), prefs.compact),
    minWidth: 380, minHeight: 560, title: 'ControlCoding Panel', backgroundColor: '#151719',
    show: false, autoHideMenuBar: true, alwaysOnTop: prefs.pinned, icon: icon(),
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), sandbox: true,
      contextIsolation: true, nodeIntegration: false, webSecurity: true, devTools: false, spellcheck: false } });
  Menu.setApplicationMenu(null);
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', event => event.preventDefault());
  window.webContents.on('will-attach-webview', event => event.preventDefault());
  window.webContents.on('render-process-gone', () => { store.consolidation?.dispose(); store.refresh.dispose(); client.cancel(); });
  window.on('close', event => { if (!quitting && tray) { event.preventDefault(); window.hide(); } });
  window.on('closed', () => { store.consolidation?.dispose(); store.refresh.dispose(); client.cancel(); window = null; });
  let saveTimer;
  const rememberBounds = () => { clearTimeout(saveTimer); saveTimer = setTimeout(() => {
    if (window && !window.isMinimized()) { prefs.bounds = window.getBounds(); savePreferences(); }
  }, 300); };
  window.on('resize', rememberBounds); window.on('move', rememberBounds);
  try {
    tray = new Tray(icon()); tray.setToolTip('ControlCoding Panel');
    tray.setContextMenu(Menu.buildFromTemplate([{ label: 'Show panel', click: show }, { label: 'Hide panel', click: () => window.hide() },
      { type: 'separator' }, { label: 'Exit', click: () => app.quit() }]));
    tray.on('double-click', show);
  } catch { tray = null; }
  registerShortcut();
  const handle = (name, callback) => ipcMain.handle(`panel:${name}`, async (event, ...args) => {
    if (!allowedSender(event, window, PAGE)) throw Error('Untrusted IPC sender');
    return callback(...args);
  });
  const noArguments = (args, callback) => { if (args.length) throw Error('Unexpected arguments'); return callback(); };
  handle('snapshot', (...args) => noArguments(args, snapshot));
  handle('external-mode', (...args) => noArguments(args, () => {
    // Switching is offered before selecting a writable project. A separate
    // process registers only the external facade, never these legacy handlers.
    if (store.value.project || store.value.busy || store.value.committing) throw Error('Close the selected project session first');
    app.relaunch({args: [...process.argv.slice(1).filter(arg => !arg.startsWith('--project=')), '--external']});
    app.quit();
    return snapshot();
  }));
  handle('knowledge',async(...args)=>{if(args.length!==2||!require('./knowledge-contract.cjs').validRequest({action:args[0],value:args[1]}))throw Error('Invalid knowledge request');await store.knowledge.run(...args);return snapshot();});
  handle('consolidation-prepare',async(...args)=>{if(args.length!==2||typeof args[0]!=='string'||!['local','api','manual'].includes(args[1]))throw Error('Invalid consolidation request');await store.consolidation.prepare(args[0],args[1]);return snapshot();});
  handle('consolidation-policy',(...args)=>noArguments(args,()=>store.consolidation.policy()));
  handle('consolidation-preview',(...args)=>{if(args.length!==2)throw Error('Invalid consolidation request');return store.consolidation.preview(...args);});
  handle('consolidation-send',async(...args)=>{if(args.length!==2)throw Error('Invalid consolidation request');await store.consolidation.send(...args);return snapshot();});
  handle('consolidation-cancel',(...args)=>noArguments(args,()=>{store.consolidation.cancel();return snapshot();}));
  handle('consolidation-copy',async(...args)=>{if(args.length!==2)throw Error('Invalid consolidation request');await store.consolidation.copy(...args);return snapshot();});
  handle('consolidation-import',async(...args)=>{if(args.length!==3)throw Error('Invalid consolidation request');await store.consolidation.import(...args);return snapshot();});
  handle('consolidation-control',async(...args)=>{if(args.length!==3)throw Error('Invalid consolidation request');await store.consolidation.control(...args);return snapshot();});
  handle('consolidation-export',async(...args)=>{if(args.length!==2)throw Error('Invalid consolidation request');const generation=store.generation,root=store.value.project,text=await store.consolidation.exportText(...args);
    const selected=await dialog.showSaveDialog(window,{title:'Export manual memory consolidation packet',defaultPath:'memory-consolidation-packet.txt',filters:[{name:'Text packet',extensions:['txt']}]});
    if(selected.canceled||!selected.filePath||generation!==store.generation||root!==store.value.project)return snapshot();
    const current=await store.consolidation.exportText(...args);
    if(generation!==store.generation||root!==store.value.project||current!==text)throw Error('consolidation_packet_stale');
    fs.writeFileSync(selected.filePath,text,{flag:'wx',mode:0o600});return snapshot();});
  handle('knowledge-resume',(...args)=>noArguments(args,()=>{store.knowledge.resume();return snapshot();}));
  handle('knowledge-answer',(...args)=>noArguments(args,async()=>{await store.knowledge.answer();return snapshot();}));
  handle('knowledge-deepen',(...args)=>noArguments(args,async()=>{await store.knowledge.deepen();return snapshot();}));
  handle('knowledge-investigate',(...args)=>noArguments(args,async()=>{await store.knowledge.investigate();return snapshot();}));
  handle('knowledge-cancel',(...args)=>noArguments(args,()=>{store.knowledge.cancel();return snapshot();}));
  handle('knowledge-retry',(...args)=>noArguments(args,async()=>{await store.knowledge.retryArchive();return snapshot();}));
  handle('knowledge-archive',async(...args)=>{
    if(args.length!==1||!['backup','restore'].includes(args[0]))throw Error('Invalid archive action');
    const action=args[0],root=store.value.project,generation=store.generation;
    if(!root||store.value.busy||store.value.knowledgeBusy)return snapshot();
    if(action==='restore'&&store.value.knowledge?.enabled)throw Error('Restore requires a project without an embedded archive');
    const selected=action==='backup'
      ?await dialog.showSaveDialog(window,{title:'Back up private project memory outside the project',defaultPath:'project-memory.ccmemory',filters:[{name:'ControlCoding memory',extensions:['ccmemory']}]})
      :await dialog.showOpenDialog(window,{title:'Restore a trusted memory backup into this empty archive',properties:['openFile','dontAddToRecent'],filters:[{name:'ControlCoding memory',extensions:['ccmemory']}]});
    if(selected.canceled||store.generation!==generation)return snapshot();
    const archive=action==='backup'?selected.filePath:selected.filePaths?.[0];if(!archive)return snapshot();
    store.value.busy=true;store.value.committing=true;store.value.knowledgeError=null;store.publish();
    try{
      const result=await new Promise((resolve,reject)=>require('node:child_process').execFile(python,
        ['-I','-B',path.join(core,'scripts','cc_knowledge.py'),'--project-root',root,'--archive',archive,action],
        {windowsHide:true,timeout:60000,maxBuffer:65536},(error,stdout)=>{
          let value;try{value=JSON.parse(stdout);}catch{return reject(Error('archive_operation_failed'));}
          if(error||value.error)return reject(Error(value.error||'archive_operation_failed'));resolve(value);
        }));
      store.value.knowledgeBackup=result;
    }catch(error){store.value.knowledgeError=error.message;}
    finally{store.value.busy=false;store.value.committing=false;store.publish();}
    if(!store.value.knowledgeError)await store.knowledge.run('status');
    return snapshot();
  });
  handle('knowledge-migrate',(...args)=>noArguments(args,async()=>{
    const root=store.value.project,generation=store.generation;
    if(!root||store.value.busy||store.value.knowledgeBusy||!store.value.knowledge?.enabled||![1,2].includes(store.value.knowledgeConsolidation?.schema))return snapshot();
    const selected=await dialog.showSaveDialog(window,{title:'Save memory backup before consolidation migration',defaultPath:'project-memory-before-consolidation.ccmemory',filters:[{name:'ControlCoding memory',extensions:['ccmemory']}]});
    if(selected.canceled||generation!==store.generation||store.value.project!==root||store.value.busy||store.value.knowledgeBusy||!selected.filePath)return snapshot();
    const archive=path.resolve(selected.filePath);
    const {physicalDirectory}=require('./profile-paths.cjs');
    const relative=path.relative(physicalDirectory(root),path.join(physicalDirectory(path.dirname(archive)),path.basename(archive)));
    if(path.extname(archive).toLowerCase()!=='.ccmemory'||relative===''||relative==='.'||!relative.startsWith('..')&&!path.isAbsolute(relative))throw Error('Choose an external .ccmemory backup outside the project');
    if(fs.existsSync(archive))throw Error('Choose a new backup path; existing files are preserved');
    store.value.busy=true;store.value.committing=true;store.value.knowledgeError=null;store.publish();
    try{
      const result=await new Promise((resolve,reject)=>require('node:child_process').execFile(python,
        ['-I','-B',path.join(core,'scripts','cc_knowledge.py'),'--project-root',root,'--archive',archive,'consolidation-migrate'],
        {windowsHide:true,timeout:120000,maxBuffer:65536},(error,stdout)=>{
          let value;try{value=JSON.parse(stdout);}catch{return reject(Error('consolidation_migration_failed'));}
          if(error||value.error)return reject(Error(value.error||'consolidation_migration_failed'));resolve(value);
        }));
      if(generation===store.generation){store.value.knowledgeBackup=result;store.value.knowledgeConsolidation=null;}
    }catch(error){if(generation===store.generation)store.value.knowledgeError=error.message;}
    finally{if(generation===store.generation){store.value.busy=false;store.value.committing=false;store.publish();}}
    if(generation===store.generation&&!store.value.knowledgeError)await store.knowledge.run('status');
    return snapshot();
  }));
  handle('choose', (...args) => noArguments(args, async () => {
    const selected = await dialog.showOpenDialog(window, { title: 'Choose a project folder', properties: ['openDirectory', 'dontAddToRecent'] });
    if (!selected.canceled && selected.filePaths.length === 1) await store.select(selected.filePaths[0]);
    return snapshot();
  }));
  handle('read', (...args) => noArguments(args, async () => { await store.observe('read'); return snapshot(); }));
  handle('preview', (...args) => noArguments(args, async () => { await store.observe('preview'); return snapshot(); }));
  handle('config-read', (...args)=>noArguments(args,async()=>{await store.configuration.run('read');return snapshot();}));
  handle('config-commit', (...args)=>noArguments(args,async()=>{await store.configuration.run('commit');return snapshot();}));
  handle('config-discard', (...args)=>noArguments(args,()=>{store.configuration.discard();return snapshot();}));
  handle('config-copy', (...args)=>noArguments(args,()=>{
    if(store.value.configAnalysis&&!store.value.busy)clipboard.writeText(JSON.stringify(store.value.configAnalysis,null,2));return snapshot();
  }));
  for(const action of ['preview','analysis','import'])handle(`config-${action}`,async(...args)=>{
    const draft=args[0],extra=args[1];
    if(args.length!==(action==='analysis'?1:2)||!require('./config-contract.cjs').validDraft(draft)||
       (action==='preview'&&!['save','apply'].includes(extra))||
       (action==='import'&&(!extra||typeof extra!=='object'||Buffer.byteLength(JSON.stringify(extra))>16000)))throw Error('Invalid configuration request');
    await store.configuration.run(action,draft,extra);return snapshot();
  });
  handle('job-preview',async(...args)=>{if(args.length!==1||!require('./panel-jobs.cjs').valid(args[0]))throw Error('Invalid job');await store.jobs.preview(args[0]);return snapshot();});
  for(const [name,method] of [['job-run','run'],['job-cancel','cancel'],['job-discard','discard']])handle(name,(...args)=>noArguments(args,async()=>{await store.jobs[method]();return snapshot();}));
  handle('ai-connect',async(...args)=>{if(args.length!==2||!['ollama','openai'].includes(args[0])||typeof args[1]!=='string'||args[1].length>512)throw Error('Invalid provider');await store.ai.connect(...args);return snapshot();});
  handle('ai-preview',(...args)=>{if(args.length!==1)throw Error('Invalid AI request');store.ai.prepare(args[0]);return snapshot();});
  for(const [name,method] of [['manual-copy','copy'],['manual-accept','accept'],['manual-discard','discardReply']])handle(name,(...args)=>noArguments(args,async()=>{await store.roles.manual[method]();return snapshot();}));
  handle('manual-preview',(...args)=>{if(args.length!==1)throw Error('Invalid manual reply');store.roles.manual.preview(args[0]);return snapshot();});
  handle('role-inspect',async(...args)=>{if(args.length!==2)throw Error('Invalid model inspection');await store.roles.inspect(...args);return snapshot();});
  handle('role-connect',async(...args)=>{if(args.length!==2)throw Error('Invalid role connection');await store.roles.connect(...args);return snapshot();});
  handle('role-disconnect',(...args)=>{if(args.length!==1)throw Error('Invalid role connection');store.roles.disconnect(args[0]);return snapshot();});
  handle('role-save',(...args)=>{if(args.length!==1)throw Error('Invalid role configuration');store.roles.save(args[0]);return snapshot();});
  handle('role-submit',async(...args)=>{if(args.length!==1)throw Error('Invalid role request');await store.roles.submit(args[0]);return snapshot();});
  for(const [name,method] of [['role-send','send'],['role-discard','discard'],['role-clear','clear'],['role-cancel','cancel'],['role-import','importProposal']])handle(name,(...args)=>noArguments(args,async()=>{await store.roles[method]();return snapshot();}));
  for(const [name,method] of [['ai-send','send'],['ai-discard','discard'],['ai-clear','clear'],['ai-disconnect','disconnect'],['ai-cancel','cancel'],['ai-import','importProposal'],['ai-retry-archive','retryArchive']])handle(name,(...args)=>noArguments(args,async()=>{await store.ai[method]();return snapshot();}));
  handle('work-preview', (...args) => noArguments(args, async () => {await store.observeWork('preview');return snapshot();}));
  handle('document-open',async(...args)=>{if(args.length!==2)throw Error('Invalid document selection');return require('./document-reader.cjs').openDocument(store,...args);});
  handle('documentation-read',async(...args)=>{if(args.length!==1||!['project','plans','handoffs'].includes(args[0]))throw Error('Invalid documentation scope');await store.documentation.read(args[0]);return snapshot();});
  handle('work-manage-preview',async(...args)=>{
    if(args.length!==1||!require('./work-manage.cjs').validValue(args[0]))throw Error('Invalid memory request');
    await store.workManagement.run('preview',args[0]);return snapshot();
  });
  handle('work-manage-commit',(...args)=>noArguments(args,async()=>{await store.workManagement.run('commit');return snapshot();}));
  handle('work-manage-discard',(...args)=>noArguments(args,()=>{store.workManagement.discard();return snapshot();}));
  handle('knowledge-sources',(...args)=>noArguments(args,async()=>{
    if(!store.value.project||store.value.busy||store.value.knowledgeBusy)return null;
    const root=store.value.project,generation=store.generation;
    const selected=await dialog.showOpenDialog(window,{title:'Select rich documents to follow',defaultPath:root,properties:['openFile','multiSelections','dontAddToRecent'],filters:[{name:'Followed documents',extensions:['docx','xlsx','pdf']}]});
    if(selected.canceled||generation!==store.generation||store.value.busy||store.value.knowledgeBusy)return null;
    const {physicalDirectory}=require('./profile-paths.cjs');
    const base=physicalDirectory(root);
    if(!selected.filePaths.length||selected.filePaths.length>128)throw Error('Select between 1 and 128 documents');
    const names=selected.filePaths.map(file=>{
      const parent=physicalDirectory(path.dirname(file)),info=fs.lstatSync(file);
      if(!info.isFile()||info.isSymbolicLink()||info.nlink!==1)throw Error('Select ordinary unlinked documents');
      const canonical=fs.realpathSync.native(path.join(parent,path.basename(file)));
      const name=path.relative(base,canonical).split(path.sep).join('/'),parts=name.split('/');
      if(!name||name.length>1024||path.isAbsolute(name)||parts.some(p=>!p||p.startsWith('.')||/[ .]$/.test(p)||/[\\:*?"<>|\x00-\x1f]/.test(p))||parts.length>1&&parts[0]!=='docs'||!['.docx','.xlsx','.pdf'].includes(path.extname(name).toLowerCase()))throw Error('Choose DOCX, XLSX or PDF files in the project root or docs folder');
      return name;
    });
    if(new Set(names.map(n=>n.toLowerCase())).size!==names.length)throw Error('Select each document only once');
    return {generation,paths:names.sort()};
  }));
  handle('work-sources',(...args)=>noArguments(args,async()=>{
    if(!store.value.project||store.value.busy)return snapshot();
    const root=store.value.project,generation=store.generation;
    const selected=await dialog.showOpenDialog(window,{title:'Select project documents to import',defaultPath:root,properties:['openFile','multiSelections','dontAddToRecent'],filters:[{name:'Documents: Markdown, text, Office and PDF',extensions:['md','txt','docx','xlsx','pdf']}]});
    if(selected.canceled||generation!==store.generation||store.value.busy)return snapshot();
    const names=selected.filePaths.map(file=>path.relative(root,file).split(path.sep).join('/'));
    if(names.length>8||names.some(p=>!p||p.length>256||path.isAbsolute(p)||p.split('/').some(c=>c.startsWith('.'))||!['.md','.txt','.docx','.xlsx','.pdf'].includes(path.extname(p).toLowerCase())))throw Error('Select up to eight ordinary project-relative supported documents');
    store.workManagement.selectSources(names);return snapshot();
  }));
  handle('work-read', async (...args) => {
    if(args.length!==1||typeof args[0]!=='string'||args[0].length>240||/[\x00-\x1f]/.test(args[0]))throw Error('Invalid memory query');
    await store.observeWork('read',args[0]);return snapshot();
  });
  for (const action of ['preview','read','refresh']) handle(`map-${action}`, (...args) => noArguments(args, async () => {
    await store.observeMap(action); return snapshot();
  }));
  handle('map-cancel', (...args) => noArguments(args, () => { store.cancelMap(); return snapshot(); }));
  handle('map-auto', (...args) => noArguments(args, () => { store.refresh.toggle(); return snapshot(); }));
  handle('map-clear-design', (...args) => noArguments(args, () => { store.setDesignPaths([]); return snapshot(); }));
  handle('map-design', (...args) => noArguments(args, async () => {
    if (!store.value.project || store.value.busy) return snapshot();
    const generation = store.generation, root = store.value.project;
    const selected = await dialog.showOpenDialog(window, {title:'Select project design documents', defaultPath:root,
      properties:['openFile','multiSelections','dontAddToRecent'], filters:[{name:'Design documents',extensions:['md','txt']}]});
    if (selected.canceled || generation !== store.generation || store.value.busy) return snapshot();
    const relative = selected.filePaths.map(file => path.relative(root,file));
    if (relative.length > 32 || relative.some(p => !p || path.isAbsolute(p) || p === '..' || p.startsWith('..'+path.sep) || !['.md','.txt','.docx','.xlsx','.pdf'].includes(path.extname(p).toLowerCase())))
      throw Error('Select at most 32 design documents inside this project');
    store.setDesignPaths(relative.map(p => p.split(path.sep).join('/')));
    await store.observeMap('preview'); return snapshot();
  }));
  handle('map-review', async (...args) => {
    if(args.length!==1 || !require('./bridge-client.cjs').validChange(args[0])) throw Error('Invalid mapping change');
    await store.review(args[0]); return snapshot();
  });
  for(const action of ['preview','read']) handle(`controls-${action}`, (...args)=>noArguments(args,async()=>{
    await store.observeControls(action);return snapshot();
  }));
  for(const action of ['preview','read']) handle(`analysis-${action}`, (...args)=>noArguments(args,async()=>{
    await store.observeAnalysis(action);return snapshot();
  }));
  handle('map-save', async (...args) => {
    if(args.length) throw Error('Unexpected arguments');
    await store.saveReview(); return snapshot();
  });
  handle('map-discard', (...args) => {
    if(args.length) throw Error('Unexpected arguments');
    store.discardReview(); return snapshot();
  });
  handle('control', (...args) => {
    if (args.length !== 1 || !['pin', 'compact', 'minimize', 'hide', 'exit'].includes(args[0])) throw Error('Unsupported control');
    const action = args[0];
    if (action === 'pin') { prefs.pinned = !prefs.pinned; window.setAlwaysOnTop(prefs.pinned); }
    if (action === 'compact') { prefs.compact = !prefs.compact; window.setBounds(visibleBounds({ ...window.getBounds(), width: 1040, height: 760 }, screen.getAllDisplays(), prefs.compact)); }
    if (action === 'minimize') window.minimize();
    if (action === 'hide') { if (tray || shortcutStatus === 'registered') window.hide(); else window.minimize(); }
    if (action === 'exit') app.quit();
    savePreferences(); notify(); return snapshot();
  });
  handle('preferences', (...args) => {
    const change = args[0];
    if (args.length !== 1 || !change || typeof change !== 'object' || Array.isArray(change) || Object.keys(change).length !== 1) throw Error('Invalid preferences');
    const [key] = Object.keys(change);
    if (!['tab', 'theme', 'shortcut'].includes(key) || safePreferences({ ...prefs, ...change })[key] !== change[key]) throw Error('Invalid preference value');
    prefs = safePreferences({ ...prefs, ...change }); if (key === 'shortcut') registerShortcut();
    savePreferences(); notify(); return snapshot();
  });
  await window.loadURL(PAGE);
  show();
  if (option('project')) await store.select(option('project'));
  return { window, store, client, snapshot, show, recall, tray, savePreferences, registerShortcut };
}
app.on('before-quit', event => { if(store?.value.committing){event.preventDefault();return;} quitting = true; store?.consolidation?.dispose();store?.knowledge?.dispose(); store?.refresh.dispose(); client?.cancel(); if (prefs) savePreferences(); });
app.on('will-quit', () => { globalShortcut.unregisterAll(); tray?.destroy(); });
app.on('window-all-closed', () => app.quit());
app.on('activate', () => { if (window) show(); });
module.exports = { start, PAGE, CSP };
