import React,{useEffect,useId,useMemo,useRef,useState} from 'react';
import {KnowledgePaper} from './knowledge-memory';

type Estimate={duration_days:number|null;buffer_days:number;earliest_start:string|null;deadline:string|null;revision:string|null;stale:boolean};
type WorkNode={id:string;title:string;path:string;revision:string;kind:string;state:string;owner:string;parents:string[];children:string[];leaf:boolean;depth:number;stage:number|null;readiness:string;blocked_by:string[];predecessors:string[];successors:string[];estimate:Estimate;earliest_start:number|null;earliest_finish:number|null;start_date:string|null;finish_date:string|null;float_days:number|null;critical:boolean};
type WorkPlan={snapshot:string;start_date:string|null;complete:boolean;critical_available:boolean;duration_days:number|null;nodes:WorkNode[];dependencies:{source:string;target:string;kind:string;origin:string}[];hierarchy:{parent:string;child:string;kind:string;origin:string}[];waves:{stage:number;ids:string[]}[];warnings:{code:string;message:string;nodes:string[]}[];notice:string};
const palette=['#6b9fe5','#b493df','#d39961','#66acb8','#b58abc'];
const readable=(s:string)=>s.replaceAll('_',' ');
const amount=(n:number|null)=>n===null?'Unknown':Number(n.toFixed(2)).toString();
const dateAt=(anchor:string|null,offset:number)=>{
 if(!anchor)return `Day ${amount(offset)}`;
 const stamp=Date.parse(anchor+'T00:00:00Z')+offset*86400000;
 return Number.isFinite(stamp)?new Date(stamp).toISOString().slice(0,10):'Outside calendar range';
};

export function KnowledgeSchedule({data,invoke}:{data:any;invoke:(fn:()=>Promise<any>)=>void}){
 const view:WorkPlan|null=data.knowledgeWorkSchedule,busy=data.busy||data.knowledgeBusy;
 const [mode,setMode]=useState<'process'|'calendar'>('process'),[selected,setSelected]=useState(''),[hover,setHover]=useState('');
 const [collapsed,setCollapsed]=useState<Set<string>>(new Set()),[allArrows,setAllArrows]=useState(false),[criticalOnly,setCriticalOnly]=useState(false),[zoom,setZoom]=useState(1);
 const [anchor,setAnchor]=useState(''),[duration,setDuration]=useState(''),[buffer,setBuffer]=useState('0'),[notBefore,setNotBefore]=useState(''),[deadline,setDeadline]=useState(''),[reason,setReason]=useState('');
 const [opened,setOpened]=useState<string|null>(null);
 const chart=useRef<HTMLDivElement>(null),[chartWidth,setChartWidth]=useState(850);
 const marker=useId().replaceAll(':','');
 const nodes=view?.nodes||[],byId=useMemo(()=>new Map(nodes.map(n=>[n.id,n])),[nodes]);
 const active=byId.get(selected)||nodes[0];
 useEffect(()=>{setSelected('');setOpened(null);setCollapsed(new Set());setHover('');setReason('');},[data.project]);
 useEffect(()=>{setAnchor(view?.start_date||'');},[view?.snapshot]);
 useEffect(()=>{const e=active?.estimate;setDuration(e?.duration_days==null?'':String(e.duration_days));setBuffer(String(e?.buffer_days||0));setNotBefore(e?.earliest_start||'');setDeadline(e?.deadline||'');setReason('');},[active?.id,view?.snapshot]);
 useEffect(()=>{if(!view)setOpened(null);},[view]);
 useEffect(()=>{if(!chart.current)return;const observer=new ResizeObserver(entries=>setChartWidth(entries[0].contentRect.width));observer.observe(chart.current);return ()=>observer.disconnect();},[!!view,!!nodes.length]);
 const call=(action:string,value:any=null)=>invoke(()=>window.panel.knowledge(action,value));
 const descendants=(id:string)=>{const result=new Set<string>(),todo=[id];while(todo.length){const next=todo.pop()!;if(result.has(next))continue;result.add(next);todo.push(...(byId.get(next)?.children||[]));}return result;};
 const ordered:WorkNode[]=[];const visited=new Set<string>();
 const inProcessOrder=(a:WorkNode,b:WorkNode)=>(a.stage??Number.MAX_SAFE_INTEGER)-(b.stage??Number.MAX_SAFE_INTEGER)||a.title.localeCompare(b.title)||a.id.localeCompare(b.id);
 const walk=(node:WorkNode)=>{if(visited.has(node.id))return;visited.add(node.id);ordered.push(node);if(!collapsed.has(node.id))node.children.map(id=>byId.get(id)).filter((n):n is WorkNode=>!!n).sort(inProcessOrder).forEach(walk);else descendants(node.id).forEach(id=>visited.add(id));};
 nodes.filter(n=>!n.parents.some(id=>byId.has(id))).sort(inProcessOrder).forEach(walk);[...nodes].sort(inProcessOrder).forEach(walk);
 const rowById=new Map(ordered.map((n,i)=>[n.id,i]));
 const visibleId=(id:string)=>{const seen=new Set<string>();while(!rowById.has(id)&&!seen.has(id)){seen.add(id);const p=byId.get(id)?.parents.find(x=>byId.has(x));if(!p)return null;id=p;}return rowById.has(id)?id:null;};
 const focus=hover||active?.id,focused=focus?descendants(focus):new Set<string>();
 const connected=new Set(focused);
 view?.dependencies.forEach(e=>{if(focused.has(e.source)||focused.has(e.target)){connected.add(e.source);connected.add(e.target);}});
 const left=270,rowHeight=48,top=68;
 const maxStage=Math.max(0,...nodes.map(n=>n.stage||0));
 const span=Math.max(1,view?.duration_days||0,...nodes.map(n=>n.earliest_finish||0));
 const timelineWidth=mode==='process'?(maxStage+1)*160*zoom:Math.max(400,chartWidth-left-90)*zoom;
 const positions=new Map<string,{x:number;y:number;w:number;unknown:boolean}>();
 ordered.forEach((n,i)=>{
  let start=n.stage||0,end=start+0.75,unknown=n.stage===null;
  if(mode==='process'&&!n.leaf){const stages=[...descendants(n.id)].map(id=>byId.get(id)).filter((x):x is WorkNode=>!!x&&x.leaf&&x.stage!==null).map(x=>x.stage!);if(stages.length){start=Math.min(...stages);end=Math.max(...stages)+.75;unknown=false;}}
  if(mode==='calendar'){start=n.earliest_start||0;end=n.earliest_finish??start;unknown=n.earliest_start===null||n.earliest_finish===null;}
  const unit=mode==='process'?160*zoom:timelineWidth/span;
  positions.set(n.id,{x:unknown?left+timelineWidth+24:left+start*unit,y:top+i*rowHeight,w:unknown?100:Math.max(8,(end-start)*unit),unknown});
 });
 const hasUnscheduled=[...positions.values()].some(p=>p.unknown),chartEnd=left+timelineWidth+(hasUnscheduled?155:85);
 const tickStep=Math.max(1,Math.ceil(span/5)),ticks=mode==='process'?Array.from({length:maxStage+1},(_,i)=>i):Array.from({length:Math.floor(span/tickStep)+1},(_,i)=>i*tickStep);
 if(mode==='calendar'&&span-ticks[ticks.length-1]>=tickStep*.5)ticks.push(span);
 const edgePairs=new Set<string>();
 const edges=(view?.dependencies||[]).flatMap(e=>{const a=visibleId(e.source),b=visibleId(e.target);if(!a||!b||a===b)return [];const key=a+'>'+b;if(edgePairs.has(key))return [];edgePairs.add(key);return [{...e,a,b}];});
 const groupColor=(n:WorkNode)=>{let current=n;const seen=new Set<string>();while(current.parents.length&&!seen.has(current.id)){seen.add(current.id);const p=byId.get(current.parents[0]);if(!p)break;current=p;}let sum=0;for(const char of current.id)sum=(sum*31+char.charCodeAt(0))>>>0;return palette[sum%palette.length];};
 const select=(id:string)=>{setSelected(id);setOpened(null);};
 const toggle=(id:string)=>setCollapsed(previous=>{const next=new Set(previous);next.has(id)?next.delete(id):next.add(id);return next;});
 const openSource=(id:string)=>{setOpened(id);call('page',id);};
 const validEstimate=(duration===''||Number.isFinite(Number(duration))&&Number(duration)>=0&&Number(duration)<=3650)&&Number.isFinite(Number(buffer))&&Number(buffer)>=0&&Number(buffer)<=365;
 function save(task:boolean){if(!view||!active)return;call('work-schedule-save',{snapshot:view.snapshot,start_date:anchor||null,task:task?{id:active.id,revision:active.revision,duration_days:duration===''?null:Number(duration),buffer_days:Number(buffer),earliest_start:notBefore||null,deadline:deadline||null}:null,reason});}
 return <section className="section work-schedule" aria-label="Work plan and Gantt">
  <div className="section-heading"><div><p className="eyebrow">WORK PROCESS</p><h2>Work plan &amp; Gantt</h2></div><button disabled={busy||!data.knowledge?.enabled} onClick={()=>call('work-schedule-view')}>Refresh work plan</button></div>
  <p>Explore main blocks, subtasks and finish-to-start dependencies. Process stages show order; the calendar shows explicit estimates.</p>
  {!data.knowledge?.enabled?<p>Enable project memory to read canonical work records and reviewed work relations.</p>:!view?<p>Read the work plan to display existing tasks. Assign document roles and approve their relations in Work traceability below, or enable Dev records in Memory settings.</p>:<>
   <p>{view.notice}</p>
   <div className="schedule-toolbar"><div role="group" aria-label="Work plan views">{(['process','calendar'] as const).map(value=><button key={value} aria-pressed={mode===value} onClick={()=>setMode(value)}>{value==='process'?'Process order':'Calendar Gantt'}</button>)}</div>
    <button disabled={!nodes.length} onClick={()=>setCollapsed(new Set())}>Expand all</button><button disabled={!nodes.length} onClick={()=>setCollapsed(new Set(nodes.filter(n=>!n.leaf).map(n=>n.id)))}>Collapse blocks</button>
    <label><input type="checkbox" checked={allArrows} onChange={e=>setAllArrows(e.target.checked)}/>All dependency arrows</label>
    <label><input type="checkbox" checked={criticalOnly} disabled={!view.critical_available} onChange={e=>setCriticalOnly(e.target.checked)}/>Highlight critical path</label>
    <label>Zoom<select value={zoom} onChange={e=>setZoom(Number(e.target.value))}>{[.75,1,1.5,2].map(value=><option key={value} value={value}>{value*100}%</option>)}</select></label>
   </div>
   <div className="schedule-metrics"><span>{nodes.filter(n=>n.leaf).length} work items</span><span>{view.dependencies.length} dependencies</span><span>{view.waves.length} process stages</span><span>{view.critical_available?`Estimated span: ${amount(view.duration_days)} calendar days`:'Critical path / float: not calculable yet'}</span></div>
   {!view.complete&&<p className="cw-notice" role="status">This graph is incomplete, stale or structurally inconsistent. Readiness and timing must be reviewed.</p>}
   {!!view.warnings.length&&<details className="schedule-warnings" open={!view.complete}><summary>Planning findings ({view.warnings.length})</summary>{view.warnings.map((w,i)=><p key={i}><strong>{readable(w.code)}</strong>: {w.message} {w.nodes.map(id=><button className="quiet" key={id} onClick={()=>select(id)}>{byId.get(id)?.title||id}</button>)}</p>)}</details>}
   <p className="schedule-legend">Block colors identify hierarchy. Green highlights selection; amber marks critical work; red marks blockers. Hatched extensions are planned buffers. Dashed extensions are calculated float.</p>
   {mode==='calendar'&&<p>Calendar days, including weekends. Dates use enclosing day boundaries; calculations retain fractional days. {view.start_date?`Project anchor: ${view.start_date}.`:'No project date set: the axis uses relative days.'} These estimates assume dependencies can be completed; unresolved blockers and resource conflicts still require action.</p>}
   {!nodes.length?<p>No work items are recorded in the current scope. Existing task identities and reviewed work relations are required; document filenames do not create tasks.</p>:<div ref={chart} className="schedule-chart" tabIndex={0} aria-label="Scrollable work dependency chart">
    <svg role="group" aria-label={mode==='process'?'Process dependency stages':'Calendar Gantt with dependencies'} width={chartEnd} height={top+ordered.length*rowHeight+25}>
     <defs><marker id={marker+'-arrow'} markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L6,3 z" fill="context-stroke"/></marker><pattern id={marker+'-buffer'} patternUnits="userSpaceOnUse" width="6" height="6"><path d="M0,6 L6,0" stroke="#bf8e50" strokeWidth="2"/></pattern></defs>
     <text x="12" y="24" fill="currentColor" fontSize="13">BLOCK / WORK ITEM</text>
     {ticks.map(value=>{const x=left+(mode==='process'?value*160*zoom:value*timelineWidth/span);return <g key={value}><line x1={x} x2={x} y1="40" y2={top+ordered.length*rowHeight} stroke="currentColor" opacity=".12"/><text x={x+4} y="24" fontSize="12" fill="currentColor">{mode==='process'?`Stage ${value+1}`:dateAt(view.start_date,value)}</text></g>;})}
     {hasUnscheduled&&<text x={left+timelineWidth+24} y="44" fontSize="11" fill="currentColor">Unscheduled</text>}
     {ordered.map((n,i)=>{const p=positions.get(n.id)!,isSelected=active?.id===n.id,dim=(criticalOnly&&view.critical_available&&!n.critical)||(hover&&!connected.has(n.id)&&![...descendants(n.id)].some(id=>connected.has(id)));const color=isSelected?'#29bc87':n.readiness==='blocked'?'#d97978':n.critical?'#dfac63':groupColor(n);const bufferWidth=mode==='calendar'&&n.leaf&&!p.unknown?Math.min(p.w,n.estimate.buffer_days*timelineWidth/span):0;const floatWidth=mode==='calendar'&&!p.unknown&&n.leaf?(n.float_days||0)*timelineWidth/span:0;
      return <g key={n.id} opacity={dim ? .25 : 1} onMouseEnter={()=>setHover(n.id)} onMouseLeave={()=>setHover('')}>
       <line x1="0" x2={chartEnd} y1={p.y+35} y2={p.y+35} stroke="currentColor" opacity=".07"/>
       {!n.leaf&&<g role="button" tabIndex={0} aria-label={`${collapsed.has(n.id)?'Expand':'Collapse'} ${n.title}`} aria-expanded={!collapsed.has(n.id)} onClick={()=>toggle(n.id)} onKeyDown={e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();toggle(n.id);}}}><text x={8+Math.min(n.depth,5)*12} y={p.y+19} fill="currentColor">{collapsed.has(n.id)?'▸':'▾'}</text></g>}
       <g role="button" tabIndex={0} aria-label={`Select task ${n.title}`} aria-pressed={isSelected} onFocus={()=>setHover(n.id)} onBlur={()=>setHover('')} onClick={()=>select(n.id)} onDoubleClick={()=>openSource(n.id)} onKeyDown={e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();select(n.id);}}} className="schedule-task">
        <title>{n.title+' · '+readable(n.readiness)+' · '+n.state+(n.critical?' · critical path':'')+(n.float_days===null?'':` · float ${amount(n.float_days)} days`)}</title>
        <text x={26+Math.min(n.depth,5)*12} y={p.y+16} fill={isSelected?'#29bc87':'currentColor'} fontSize="12" fontWeight={n.leaf?400:700}>{n.title.slice(0,Math.max(13,31-n.depth*2))}{n.title.length>31-n.depth*2?'…':''}</text>
        <text x={26+Math.min(n.depth,5)*12} y={p.y+30} fill="currentColor" opacity=".65" fontSize="10">{readable(n.readiness)}{n.critical?' · CRITICAL':''}</text>
        <rect x={p.x} y={p.y+3} width={p.w} height="24" rx="4" fill={color} fillOpacity={p.unknown ? .15 : .28} stroke={color} strokeWidth={isSelected?2:1} strokeDasharray={p.unknown?'4 3':undefined}/>
        {bufferWidth>0&&<rect x={p.x+p.w-bufferWidth} y={p.y+3} width={bufferWidth} height="24" fill={`url(#${marker}-buffer)`}/>}
        {floatWidth>0&&<rect x={p.x+p.w} y={p.y+10} width={floatWidth} height="10" fill="none" stroke={color} strokeDasharray="3 3"/>}
        <defs><clipPath id={marker+'-bar-'+i}><rect x={p.x+4} y={p.y+3} width={Math.max(0,p.w-8)} height="24"/></clipPath></defs>
        <text clipPath={`url(#${marker}-bar-${i})`} x={p.x+5} y={p.y+20} fontSize="10" fill="currentColor">{p.unknown?'Unscheduled':mode==='process'?(n.leaf?'Task':'Block'):n.leaf?`${amount(n.estimate.duration_days)}d${n.estimate.buffer_days?` + ${amount(n.estimate.buffer_days)}d buffer`:''}`:'Block span'}</text>
       </g>
      </g>;
     })}
     {edges.map((e,i)=>{const a=positions.get(e.a)!,b=positions.get(e.b)!,highlight=focused.has(e.source)||focused.has(e.target)||focused.has(e.a)||focused.has(e.b);if(!allArrows&&!highlight)return null;const sx=a.x+a.w,sy=a.y+15,tx=b.x,ty=b.y+15,direction=ty>=sy?1:-1,bend=sx+8+(i%3)*3,lane=sy+direction*(20+(i%3)*3);const stroke=highlight?'#29bc87':byId.get(e.source)?.critical&&byId.get(e.target)?.critical?'#dfac63':'#7792ac';const route=tx-sx>32?`M${sx},${sy} H${bend} Q${bend+4},${sy} ${bend+4},${sy+direction*4} V${ty-direction*4} Q${bend+4},${ty} ${bend+8},${ty} H${tx-3}`:`M${sx},${sy} H${bend} Q${bend+4},${sy} ${bend+4},${sy+direction*4} V${lane-direction*4} Q${bend+4},${lane} ${bend},${lane} H${tx-14} Q${tx-18},${lane} ${tx-18},${lane+direction*4} V${ty-direction*4} Q${tx-18},${ty} ${tx-14},${ty} H${tx-3}`;return <path className="schedule-dependency" key={i} d={route} fill="none" stroke={stroke} strokeWidth={highlight?2:1} opacity={hover&&!highlight ? .1 : .8} markerEnd={`url(#${marker}-arrow)`} pointerEvents="none"><title>{`${byId.get(e.source)?.title} must finish before ${byId.get(e.target)?.title} starts · ${e.origin}`}</title></path>;})}
    </svg>
   </div>}
   {!!view.waves.length&&<details><summary>Parallel work by process stage</summary><p>Dependency-compatible work may run in parallel. Shared owners and available people or agents can still restrict execution.</p><div className="schedule-waves">{view.waves.map(w=><article key={w.stage}><strong>Stage {w.stage+1}</strong>{w.ids.map(id=><button key={id} onClick={()=>select(id)}>{byId.get(id)?.title||id}</button>)}</article>)}</div></details>}
   {active&&<aside className="schedule-detail" aria-label="Selected work item">
    <h3>{active.title}</h3><p><strong>{readable(active.readiness)}</strong> · Recorded state: {active.state} · Owner: {active.owner}</p><code>{active.path}</code>
    <div className="schedule-related"><div><h4>Must finish first</h4>{active.predecessors.length?active.predecessors.map(id=><button key={id} onClick={()=>select(id)}>{byId.get(id)?.title||id}</button>):<p>No recorded predecessor.</p>}</div><div><h4>Unlocks next</h4>{active.successors.length?active.successors.map(id=><button key={id} onClick={()=>select(id)}>{byId.get(id)?.title||id}</button>):<p>No recorded successor.</p>}</div></div>
    {!!active.blocked_by.length&&<p className="cw-notice">Blocked by: {active.blocked_by.map(id=>byId.get(id)?.title||id).join(', ')}</p>}
    <p>{active.start_date?`${active.start_date} → ${active.finish_date}`:'Calendar dates not determined'} · Float: {active.float_days===null?'not calculated':`${amount(active.float_days)} calendar days`}{active.critical?' · On the critical path':''}</p>
    <button disabled={busy} onClick={()=>openSource(active.id)}>Open original work record</button>
    <details className="schedule-estimates"><summary>Planning estimates and buffers</summary><p>Saved estimates describe this source revision. They do not change the recorded task state. Groups roll up their children; estimate the leaf work items.</p>
     {active.estimate.stale&&<p className="cw-notice">The source changed. Review and save these estimates against its new revision.</p>}
     <div className="schedule-fields"><label>Project start date<input type="date" value={anchor} onChange={e=>setAnchor(e.target.value)}/></label>
      <label>Estimated duration (calendar days)<input type="number" min="0" max="3650" step="0.25" value={duration} disabled={!active.leaf} placeholder="Unknown" onChange={e=>setDuration(e.target.value)}/></label>
      <label>Planned buffer (calendar days)<input type="number" min="0" max="365" step="0.25" value={buffer} disabled={!active.leaf} onChange={e=>setBuffer(e.target.value)}/></label>
      <label>Start no earlier than<input type="date" value={notBefore} disabled={!active.leaf} onChange={e=>setNotBefore(e.target.value)}/></label>
      <label>Target deadline<input type="date" value={deadline} disabled={!active.leaf} onChange={e=>setDeadline(e.target.value)}/></label></div>
     <label>Reason for this planning change<textarea value={reason} maxLength={2000} onChange={e=>setReason(e.target.value)}/></label>
     <div className="schedule-toolbar"><button disabled={busy||!active.leaf||!validEstimate||reason.trim().length<5} onClick={()=>save(true)}>Save selected estimate</button><button disabled={busy||reason.trim().length<5} onClick={()=>save(false)}>Save project date only</button></div>
     <p>Float is calculated from dependencies; a planned buffer is a reserve you choose. Neither is verified completion.</p>
    </details>
   </aside>}
   {opened&&<aside className="knowledge-wiki-drawer" aria-label="Work plan source document"><button onClick={()=>setOpened(null)}>Close work document</button>{data.knowledgePage?.id===opened?<KnowledgePaper text={data.knowledgePage.body}/>:<p>{data.knowledgeError||'Reading original record…'}</p>}</aside>}
  </>}
 </section>;
}
