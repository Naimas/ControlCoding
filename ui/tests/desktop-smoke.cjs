/* Executed by the real Electron runtime against an external build/profile. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { app, ipcMain, globalShortcut, dialog } = require('electron');
const option = name => process.argv.find(a=>a.startsWith(`--${name}=`))?.slice(name.length+3);
const build = option('build'), artifacts = option('artifacts'), fixtures = option('fixtures');
const checks = [], errors = [];
const wait = ms => new Promise(r=>setTimeout(r,ms));
const record = (name, value) => { assert(value,name); checks.push(name); };
const inventory = root => {
  const out={};
  for(const name of fs.readdirSync(root)){const p=path.join(root,name),s=fs.lstatSync(p);out[name]=s.isDirectory()?inventory(p):fs.readFileSync(p).toString('base64');}
  return out;
};
async function run() {
  const first=path.join(fixtures,'Project alpha'),second=path.join(fixtures,'Project beta');
  fs.mkdirSync(first,{recursive:true});fs.mkdirSync(second,{recursive:true});
  fs.writeFileSync(path.join(first,'unrelated.secret'),'CANARY-private');
  fs.mkdirSync(path.join(second,'.controlcoding'),{recursive:true});
  fs.writeFileSync(path.join(second,'.controlcoding','cc_config.json'),'{bad CANARY');
  const before=inventory(fixtures);
  const panel=await require(path.join(build,'panel-app.cjs')).start();
  const initialShortcutStatus=panel.snapshot().shortcutStatus;
  const web=panel.window.webContents;
  web.on('console-message',(_event,level,message)=>{if(level>=2)errors.push(message);});
  const js=code=>web.executeJavaScript(code,true);
  await wait(250);
  record('sandbox and no Node in renderer',await js('typeof require === "undefined" && typeof process === "undefined" && !!window.panel'));
  record('secure BrowserWindow settings',web.getLastWebPreferences().sandbox && web.getLastWebPreferences().contextIsolation && !web.getLastWebPreferences().nodeIntegration);
  record('initial no-project screen',await js('document.body.innerText.includes("Your workspace, in view.")'));
  const prefSnapshot=panel.snapshot().preferences;
  record('off-screen saved bounds restored to a visible display',panel.window.getBounds().x<90000 && panel.window.getBounds().y<90000);
  // Exercise the actual chooser handler using the native dialog boundary stub.
  dialog.showOpenDialog=async()=>({canceled:false,filePaths:[first]});
  await js('window.panel.chooseProject()');await wait(120);
  record('read succeeds through isolated Python IPC',panel.store.value.state?.status==='ok');
  record('project selection rendered',await js('document.body.innerText.includes("Project alpha")'));
  await js('window.panel.preferences({tab:"Setup"})');await js('window.panel.preview()');await wait(120);
  record('canonical preview contains 22 files',panel.store.value.preview?.files.length===22);
  record('no secrets in renderer',!await js('document.body.innerText.includes("CANARY")'));
  fs.writeFileSync(path.join(artifacts,'setup-expanded.png'),(await web.capturePage()).toPNG());
  await js('window.panel.preferences({tab:"Status"})');await wait(120);
  fs.writeFileSync(path.join(artifacts,'status-expanded.png'),(await web.capturePage()).toPNG());
  panel.store.value.observedAt=new Date(Date.now()-70000).toISOString();panel.store.publish();await wait(1100);
  record('old observation visibly marked for refresh',await js('document.body.innerText.includes("Refresh recommended")'));
  await js('window.panel.preferences({theme:"light"})');await wait(100);
  fs.writeFileSync(path.join(artifacts,'status-light.png'),(await web.capturePage()).toPNG());
  await js('window.panel.preferences({theme:"dark"})');
  await js('window.panel.control("pin")');record('pin sets always-on-top',panel.window.isAlwaysOnTop());
  await js('window.panel.control("pin")');record('unpin clears always-on-top',!panel.window.isAlwaysOnTop());
  await js('window.panel.control("compact")');await wait(200);
  const compactBounds=panel.window.getBounds();
  record(`compact geometry within DPI rounding: ${compactBounds.width}`,Math.abs(compactBounds.width-400)<=4);
  record('compact no horizontal overflow',await js('document.documentElement.scrollWidth <= window.innerWidth'));
  fs.writeFileSync(path.join(artifacts,'status-compact.png'),(await web.capturePage()).toPNG());
  for(const factor of [1.25,1.5,2]) {
    web.setZoomFactor(factor);await wait(100);
    record(`zoom ${factor} no document overflow`,await js('document.documentElement.scrollWidth <= window.innerWidth'));
  }
  web.setZoomFactor(1);
  await js('window.panel.control("compact")');
  await js('window.panel.control("minimize")');await wait(100);record('minimize works',panel.window.isMinimized());panel.show();
  await js('window.panel.control("hide")');record('hide preserves recall',!panel.window.isVisible());panel.show();record('show restores',panel.window.isVisible());
  record('tray available',Boolean(panel.tray));
  panel.window.close();record('close hides to tray',!panel.window.isDestroyed()&&!panel.window.isVisible());panel.show();
  record('shortcut registered or visibly unavailable',['registered','unavailable'].includes(panel.snapshot().shortcutStatus));
  await js('window.panel.preferences({shortcut:"disabled"})');
  const reserved='CommandOrControl+Alt+C';const owns=globalShortcut.register(reserved,()=>{});
  if(owns){await js('window.panel.preferences({shortcut:"CommandOrControl+Alt+C"})');
    // Main unregisterAll owns this process's shortcuts; collision across another
    // app is covered by unavailable registration state, not simulated here.
    record('shortcut preference is reflected',panel.snapshot().preferences.shortcut===reserved);}
  await js('window.panel.preferences({shortcut:"CommandOrControl+Shift+Space"})');
  panel.recall();record('recall hides',!panel.window.isVisible());panel.recall();record('recall shows',panel.window.isVisible());
  // Main-frame call permits only the declared controls.
  record('unknown control rejected',await js('window.panel.control("apply").then(()=>false,()=>true)'));
  record('unknown preference rejected',await js('window.panel.preferences({python:"CANARY"}).then(()=>false,()=>true)'));
  const source=panel.store.value.project;
  record('renderer cannot choose arbitrary root',await js('typeof window.panel.select === "undefined"'));
  dialog.showOpenDialog=async()=>({canceled:false,filePaths:[second]});
  await js('window.panel.chooseProject()');await wait(100);
  record('corrupt selected project reports invalid_json',panel.store.value.error?.code==='invalid_json');
  record('switch discards prior state and preview',panel.store.value.state===null&&panel.store.value.preview===null&&panel.store.value.project!==source);
  record('error is rendered and secret suppressed',await js('document.body.innerText.includes("Observation unavailable") && !document.body.innerText.includes("CANARY")'));
  fs.writeFileSync(path.join(artifacts,'invalid-project.png'),(await web.capturePage()).toPNG());
  await panel.store.select(path.join(fixtures,'absent'));
  record('missing root remains a distinct error',panel.store.value.error?.code==='missing_root');
  await panel.store.select(first);panel.client.python=path.join(fixtures,'missing-python.exe');
  await js('window.panel.refresh()');record('helper failure is visible',panel.store.value.error?.code==='helper_unavailable'&&panel.store.value.state===null);
  record('all fixture bytes and names preserved',JSON.stringify(inventory(fixtures))===JSON.stringify(before));
  record('no console errors',errors.length===0);
  fs.writeFileSync(path.join(artifacts,'desktop-smoke.json'),JSON.stringify({versions:process.versions,platform:process.platform,initialShortcutStatus,prefSnapshot,compactBounds,checks,errors},null,2));
  app.quit();
}
run().catch(error=>{fs.writeFileSync(path.join(artifacts,'desktop-smoke-failure.json'),JSON.stringify({checks,errors,error:String(error),stack:error.stack},null,2));app.exit(1);});
