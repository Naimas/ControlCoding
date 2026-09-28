import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ProjectMap } from './project-map';
import { ControlWork } from './controlwork';
import { SetupConfiguration } from './setup-configuration';
import { Checks, InstallWizard } from './execution';
import { AISessions } from './ai-sessions';

type Tab = 'Status' | 'Project Map' | 'Activity' | 'Setup' | 'Agents' | 'Memory' | 'Checks';
type Snapshot = { project: string | null; generation: number; busy: boolean; state: any; preview: any;
  configuration:any;configPreview:any;configAnalysis:any;configError:any;configSaved:any;configDraftVersion:number;
  aiConnection:any;aiModels:string[];aiMessages:any[];aiPreview:any;aiError:any;aiArchive:any;aiIncomplete:boolean;jobPlan:any;jobRun:any;jobError:any;
  manualHandoff:any;manualReplyPreview:any;manualError:string|null;manualResult:any;rolePromptTemplates:any;roleConfig:any;roleConfigVersion:number;roleConnections:any;roleSaved:boolean;roleError:string|null;roleCounts:any;rolePreview:any;roleResult:any;
  roleConfigError:string|null;roleExecution:any;roleEvents:any[];
  documentation:any;documentationScope:string;documentationError:any;documentationObservedAt:string|null;documentationReading:boolean;
  work:any;workScope:any;workError:any;workObservedAt:string|null;
  knowledge?:any;knowledgeBusy?:boolean;knowledgeConsolidationExecution?:any;knowledgeConsolidationSettings?:any;knowledgeConsolidationQueue?:any;knowledgeConsolidationContext?:any;
  map:any;mapScope:any;mapError:any;mapObservedAt:string|null;designPaths:string[];
  reviewPreview:any;reviewError:any;reviewSaved:any;committing:boolean;controlsScope:any;analysisScope:any;
  mapRefresh:any;mapHistory:any[];mapOrder:string[];
  error: {code: string; source: string} | null; observedAt: string | null;
  activity: {operation:string;outcome:string;time:string;code:string|null}[];
  preferences: {pinned:boolean;compact:boolean;tab:Tab;theme:string;shortcut:string};
  shortcutStatus:string;preferenceStatus:string;trayAvailable:boolean;runtime:{electron:string;platform:string}; };
declare global { interface Window { panel: { snapshot():Promise<Snapshot>;chooseProject():Promise<Snapshot>;
  externalMode():Promise<Snapshot>;
  refresh():Promise<Snapshot>;preview():Promise<Snapshot>; control(action:string):Promise<Snapshot>;
  configRead():Promise<Snapshot>;configCommit():Promise<Snapshot>;configDiscard():Promise<Snapshot>;configCopy():Promise<Snapshot>;
  configPreview(draft:object,intent:string):Promise<Snapshot>;configAnalysis(draft:object):Promise<Snapshot>;configImport(draft:object,proposal:object):Promise<Snapshot>;
  jobPreview(value:object):Promise<Snapshot>;jobRun():Promise<Snapshot>;jobCancel():Promise<Snapshot>;jobDiscard():Promise<Snapshot>;
  aiConnect(provider:string,key:string):Promise<Snapshot>;aiPreview(value:object):Promise<Snapshot>;aiSend():Promise<Snapshot>;aiDiscard():Promise<Snapshot>;aiClear():Promise<Snapshot>;aiDisconnect():Promise<Snapshot>;aiCancel():Promise<Snapshot>;aiImport():Promise<Snapshot>;aiRetryArchive():Promise<Snapshot>;
  manualCopy():Promise<Snapshot>;manualAccept():Promise<Snapshot>;manualDiscard():Promise<Snapshot>;manualPreview(value:object):Promise<Snapshot>;roleInspect(provider:string,model:string):Promise<Snapshot>;roleConnect(provider:string,key:string):Promise<Snapshot>;roleDisconnect(provider:string):Promise<Snapshot>;roleSave(value:object):Promise<Snapshot>;roleSubmit(value:object):Promise<Snapshot>;roleSend():Promise<Snapshot>;roleDiscard():Promise<Snapshot>;roleClear():Promise<Snapshot>;roleCancel():Promise<Snapshot>;roleImport():Promise<Snapshot>;
  workPreview():Promise<Snapshot>;workRead(query:string):Promise<Snapshot>;
  knowledge(action:string,value?:any):Promise<Snapshot>;knowledgeResume():Promise<Snapshot>;knowledgeAnswer():Promise<Snapshot>;knowledgeDeepen():Promise<Snapshot>;knowledgeInvestigate():Promise<Snapshot>;knowledgeCancel():Promise<Snapshot>;knowledgeRetry():Promise<Snapshot>;
  consolidationPrepare(job:string,mode:'local'|'api'|'manual'):Promise<Snapshot>;consolidationPolicy():Promise<any>;consolidationPreview(job:string,requestId:string):Promise<any>;consolidationSend(job:string,requestId:string):Promise<Snapshot>;consolidationCancel():Promise<Snapshot>;consolidationCopy(job:string,requestId:string):Promise<Snapshot>;consolidationExport(job:string,requestId:string):Promise<Snapshot>;consolidationImport(job:string,requestId:string,text:string):Promise<Snapshot>;consolidationControl(job:string,requestId:string,operation:string):Promise<Snapshot>;
  knowledgeArchive(action:'backup'|'restore'):Promise<Snapshot>;
  knowledgeMigrate():Promise<Snapshot>;
  knowledgeChooseSources():Promise<{generation:number;paths:string[]}|null>;
  documentOpen(generation:number,id:string):Promise<any>;
  documentationRead(scope:string):Promise<Snapshot>;
  workManagePreview(value:object):Promise<Snapshot>;workManageCommit():Promise<Snapshot>;workManageDiscard():Promise<Snapshot>;workChooseSources():Promise<Snapshot>;
  mapPreview():Promise<Snapshot>;mapRead():Promise<Snapshot>;mapRefresh():Promise<Snapshot>;mapCancel():Promise<Snapshot>;
  mapAuto():Promise<Snapshot>;
  chooseDesign():Promise<Snapshot>;clearDesign():Promise<Snapshot>;
  reviewMapping(change:object):Promise<Snapshot>;saveMapping():Promise<Snapshot>;discardMapping():Promise<Snapshot>;
  controlsPreview():Promise<Snapshot>;controlsRead():Promise<Snapshot>;
  analysisPreview():Promise<Snapshot>;analysisRead():Promise<Snapshot>;
  preferences(value:object):Promise<Snapshot>;subscribe(callback:(value:Snapshot)=>void):()=>void; }; } }
const tabs: Tab[] = ['Status','Project Map','Activity','Setup','Agents','Memory','Checks'];
const symbols: Record<Tab,string> = {'Project Map':'#',Status:'◉',Activity:'≋',Setup:'⊞',Agents:'◇',Memory:'▤',Checks:'✓'};
const copy = {
  title:'ControlCoding', subtitle:'LOCAL PANEL', choose:'Open project', refresh:'Refresh observation',
  preview:'Preview minimal init', noProject:'Your workspace, in view.',
  noProjectBody:'Choose a project to inspect its ControlCoding setup and review its code map. Mapping choices save only after a preview.',
  readOnly:'LOCAL PANEL', unknown:'Not observed', host:'Host delivery is unverified',
};
const descriptions: Record<string,string> = {
  plan_conflict:'An existing file needs reconciliation before minimal init can proceed.',
  invalid_json:'A setup file contains invalid JSON. Its original contents have been preserved.',
  invalid_config:'A setup file has an unsupported field type or value.', invalid_type:'A setup input has an invalid type.',
  unsupported_path:'A link, special file or unsupported path prevents this observation.',
  missing_root:'This project folder is no longer available.', inaccessible:'An input is inaccessible or open for writing. Retry after inspecting it.',
  changed_input:'An input changed during observation. Refresh to inspect it again.', too_large:'A setup input exceeds the bounded read budget.',
  helper_timeout:'The local helper exceeded its time limit. You can retry.', helper_unavailable:'The configured Python helper could not start.',
  helper_failure:'The local helper stopped unexpectedly. You can retry.', cancelled:'The observation was cancelled.',
};
const shortName = (p:string) => p.split(/[\\/]/).filter(Boolean).at(-1) || p;
const time = (value:string) => new Date(value).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});
function Icon({name}:{name:string}) { return <span aria-hidden="true" className="icon">{name}</span>; }
function App() {
  const [data,setData] = useState<Snapshot|null>(null), [clock,setClock] = useState(Date.now()), [uiError,setUiError] = useState(false);
  useEffect(() => { const off = window.panel.subscribe(setData); window.panel.snapshot().then(setData).catch(()=>setUiError(true));
    const tick=setInterval(()=>setClock(Date.now()),1000); return()=>{off();clearInterval(tick);}; },[]);
  const invoke = (fn:()=>Promise<Snapshot>) => { setUiError(false); fn().catch(()=>setUiError(true)); };
  if (!data) return <main className="loading"><div className="brandmark">CC</div><h1>{copy.title}</h1><p>{uiError?'The panel could not connect to its local process.':'Starting the local observer…'}</p></main>;
  const tab=data.preferences.tab, root=data.project, state=data.state, plan=data.preview;
  const observed=tab==='Memory'?(data.knowledge?.last_reconcile||data.workObservedAt):tab==='Project Map'?data.mapObservedAt:data.observedAt;
  const age=observed?Math.max(0,Math.floor((clock-Date.parse(observed))/1000)):null;
  const stale=age!==null&&age>=60;
  const sourceCount=state?.sources.filter((s:any)=>s.state!=='absent').length||0;
  const selectTab=(name:Tab)=>invoke(()=>window.panel.preferences({tab:name}));
  const config=state?.configuration;
  return <div className={`shell ${data.preferences.compact?'compact':''} theme-${data.preferences.theme}`}>
    <aside className="sidebar"><div className="branding"><div className="brandmark">CC</div><div><b>{copy.title}</b><small>{copy.subtitle}</small></div></div>
      <div className="nav-label">WORKSPACE</div><nav aria-label="Panel sections">{tabs.map(name=><button key={name} className={tab===name?'nav active':'nav'} aria-current={tab===name?'page':undefined} onClick={()=>selectTab(name)}><Icon name={symbols[name]}/><span>{name==='Memory'?'ControlWork':name==='Agents'?'AI & Sessions':name}</span>{name==='Setup'&&<span className="nav-dot"/>}</button>)}</nav>
      <div className="sidebar-bottom"><span className="live-dot"/> Local session<div className="muted">{[...new Set([...(data.aiConnection?[data.aiConnection]:[]),...Object.entries(data.roleConnections||{}).filter(([,v]:any)=>v.connected).map(([id])=>id)])].join(' + ')||'No provider connected'}</div><button className="quiet theme" onClick={()=>invoke(()=>window.panel.preferences({theme:data.preferences.theme==='dark'?'light':'dark'}))}>Switch to {data.preferences.theme==='dark'?'light':'dark'} theme</button></div>
    </aside>
    <div className="workspace"><header className="topbar"><div className="project-heading"><span className="folder" aria-hidden="true">▱</span><div><strong>{root?shortName(root):'No project selected'}</strong><span className="project-path" title={root||''}>{root||'Choose a folder to get started'}</span></div></div>
      <div className="window-actions"><button className={data.preferences.pinned?'tool selected':'tool'} title={data.preferences.pinned?'Unpin window':'Pin window'} aria-label={data.preferences.pinned?'Unpin window':'Pin window'} aria-pressed={data.preferences.pinned} onClick={()=>invoke(()=>window.panel.control('pin'))}>⌖</button>
        <button className="tool" title={data.preferences.compact?'Expand panel':'Compact panel'} aria-label={data.preferences.compact?'Expand panel':'Compact panel'} onClick={()=>invoke(()=>window.panel.control('compact'))}>▣</button>
        <button className="tool" title="Minimize" aria-label="Minimize" onClick={()=>invoke(()=>window.panel.control('minimize'))}>−</button>
        <button className="tool" title="Hide to tray" aria-label="Hide to tray" onClick={()=>invoke(()=>window.panel.control('hide'))}>⌄</button></div></header>
      <main><div className="page-heading"><div><p className="eyebrow">PROJECT OBSERVER</p><h1>{tab==='Memory'?'ControlWork':tab==='Agents'?'AI & Sessions':tab}</h1></div><span className="badge">{copy.readOnly}</span></div>
        {uiError&&<div className="error" role="alert">The panel could not complete this action. Try again.</div>}
        {data.error&&tab!=='Project Map'&&tab!=='Memory'&&<div className="error" role="alert"><strong>Observation unavailable</strong><p>{descriptions[data.error.code]||'This operation is unavailable for the selected input.'}</p><code>{data.error.code} · {data.error.source}</code></div>}
        {!root?<section className="welcome"><div className="orbit"><span>CC</span></div><h2>{copy.noProject}</h2><p>{copy.noProjectBody}</p><button className="primary" onClick={()=>invoke(window.panel.chooseProject)}>＋ {copy.choose}</button><div className="welcome-note">Local Python service · Writes require preview · No model required</div></section>:<>
        <div className="project-toolbar"><span className={stale?'observation stale':'observation'}>{data.busy||tab==='Memory'&&data.knowledgeBusy?'Operation in progress…':observed?`${stale?'Refresh recommended':'Observed'} · ${age}s ago`:copy.unknown}</span><div><button className="quiet" onClick={()=>invoke(window.panel.chooseProject)}>Change project</button><button className="secondary" disabled={data.busy||tab==='Memory'&&data.knowledgeBusy} onClick={()=>invoke(tab==='Setup'?window.panel.configRead:tab==='Memory'?(data.knowledge?.enabled?()=>window.panel.knowledge('sync'):data.workScope?()=>window.panel.workRead(data.work?.query||''):window.panel.workPreview):tab==='Project Map'?(data.mapScope?window.panel.mapRefresh:window.panel.mapPreview):window.panel.refresh)}>↻ Refresh</button></div></div>
        <div hidden={tab!=='Memory'}><ControlWork key={data.generation} data={data} invoke={invoke}/></div>
        {tab==='Project Map'&&<ProjectMap key={data.generation} data={data} invoke={invoke} clock={clock}/>}
        {tab==='Status'&&<><section className="overview"><div className="overview-text"><span className="eyebrow">{state?'WORKSPACE OBSERVATION':'READY TO OBSERVE'}</span><h2>{state?(sourceCount?'Your setup, made visible.':'A fresh starting point.'):'Let’s inspect your project.'}</h2><p>{state?(sourceCount?'Review the configuration found in this folder, then inspect what minimal init would create or keep.':'No selected setup files were found. Preview the minimal Core structure before making any changes.'):'Refresh to inspect the selected setup files. No project content is changed.'}</p><button className="primary" disabled={data.busy} onClick={()=>{selectTab('Setup');invoke(window.panel.configRead);}}>Preview setup <span aria-hidden="true">↗</span></button></div><div className="scope-stamp"><div className="scope-ring">CC</div><span>LOCAL OBSERVER</span><small>Project stays in your control</small></div></section>
          <div className="metrics"><div><span>Setup sources</span><b>{state?sourceCount:'—'} <small>/ 6</small></b><em>{state?'Bounded files inspected':'Not inspected'}</em></div><div><span>Hook events</span><b>{config?config.hook_event_count:'—'}</b><em>Configured, not delivery-tested</em></div><div><span>Protected zones</span><b>{config?config.protected_zone_count:'—'}</b><em>Configuration count only</em></div></div>
          <section className="section"><div className="section-heading"><h3>Observation boundary</h3><span className="small muted">No automatic execution</span></div><div className="boundary"><Icon name="✓"/><div><b>Observation and explicit setup</b><p>Status observes configuration and context. Setup provides a separate draft and reviewed Core apply. No memory initialization or agent execution runs here.</p></div><span className="pill">Local</span></div><div className="boundary"><Icon name="◇"/><div><b>{copy.host}</b><p>A configured hook is not evidence that your editor loaded it.</p></div><span className="pill muted">Unverified</span></div></section></>}
        <div hidden={tab!=='Setup'}><SetupConfiguration key={data.generation} data={data} invoke={invoke}/><InstallWizard key={'wizard-'+data.generation} data={data} invoke={invoke}/></div>
        <div hidden={tab!=='Checks'}><Checks key={data.generation} data={data} invoke={invoke}/></div>
        {tab==='Activity'&&<section className="section"><div className="section-heading"><h2>Panel activity</h2><span className="small muted">This project · This session</span></div><p className="muted">Only actions performed in this panel are shown. External editor activity is not observed.</p>{data.activity.length?data.activity.map((event,i)=><div className="activity-row" key={i}><span className="activity-dot"/><div><b>{event.operation.startsWith('job_')?'Core '+event.operation.slice(4):event.operation.startsWith('config_')?'Configuration '+event.operation.slice(7):event.operation.startsWith('work_')?'ControlWork '+event.operation.slice(5):event.operation.startsWith('map_')?'Project Map '+event.operation.slice(4):event.operation==='read'?'Setup observation':'Minimal init preview'}</b><p>{event.outcome}{event.code?` · ${event.code}`:''}</p></div><time>{time(event.time)}</time></div>):<p>No panel activity yet.</p>}</section>}
        <div hidden={tab!=='Agents'}><AISessions key={data.generation} data={data} invoke={invoke}/></div>
        </>}
        <details className="panel-settings"><summary>Panel preferences</summary><label>Recall shortcut<select value={data.preferences.shortcut} onChange={e=>invoke(()=>window.panel.preferences({shortcut:e.target.value}))}><option value="CommandOrControl+Shift+Space">Ctrl + Shift + Space</option><option value="CommandOrControl+Alt+C">Ctrl + Alt + C</option><option value="disabled">Disabled</option></select></label><p>{data.shortcutStatus==='registered'?'Shortcut registered.':data.shortcutStatus==='disabled'?'Shortcut disabled.':'Shortcut unavailable; use the tray to recall the panel.'} {data.trayAvailable?'Tray available.':'Tray unavailable; Hide minimizes when no recall route exists.'}</p>{data.preferenceStatus!=='available'&&<p>Preferences: {data.preferenceStatus.replaceAll('_',' ')}. Current session remains usable.</p>}<button className="quiet" onClick={()=>invoke(()=>window.panel.control('exit'))}>Exit panel</button></details>
      </main><footer><span><span className="live-dot"/> Local panel</span><span>Explicit actions · Reviewed changes</span><span className="footer-version">PROJECT MAP</span></footer>
    </div>
  </div>;
}
createRoot(document.getElementById('root')!).render(<App/>);
