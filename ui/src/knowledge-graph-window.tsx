import React,{useState} from 'react';
export function KnowledgeGraphWindow({data,invoke}:{data:any;invoke:(f:()=>Promise<any>)=>void}){
 const g=data.knowledgeCatalog?.graph;
 const [search,setSearch]=useState('');
 if(!g)return null;
 const load=(change:any)=>invoke(()=>window.panel.knowledge('graph-view',{topic:g.topic,query:g.query,focus:null,offset:0,snapshot:null,...change}));
 return <section className="knowledge-controls" aria-label="Archive graph navigation">
  <p>{g.source_total} indexed sources. This canvas shows a bounded window; {g.external_edges} incident connections are outside the loaded view.</p>
  <label>Archive category<select disabled={data.knowledgeBusy} value={g.topic||''} onChange={e=>load({topic:e.target.value||null})}><option value="">All sources ({g.source_total})</option>{g.topics.map((t:any)=><option key={t.id} value={t.id}>{t.title} ({t.count})</option>)}</select></label>
  <label>Search the entire archive<input maxLength={200} value={search} onChange={e=>setSearch(e.target.value)}/></label>
  <button disabled={data.knowledgeBusy} onClick={()=>load({query:search})}>Search archive</button>
  <button disabled={data.knowledgeBusy||!g.offset} onClick={()=>load({offset:Math.max(0,g.offset-200),snapshot:g.snapshot,focus:g.focus})}>Previous sources</button>
  <span>{g.total?g.offset+1:0}–{Math.min(g.offset+200,g.total)} of {g.total}</span>
  <button disabled={data.knowledgeBusy||g.next_offset===null} onClick={()=>load({offset:g.next_offset,snapshot:g.snapshot,focus:g.focus})}>Next sources</button>
  <p className="small muted">Categories use explained rules on source kind, path and title. Paging replaces the detail window on this canvas; it does not retain the whole archive.</p>
 </section>;
}
