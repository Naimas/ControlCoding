'use strict';
// Whole terms describe a subject or document purpose. More specific subjects
// outrank generic words such as "schema" or "panel"; equal evidence stays pending.
const rules=[
 ['Development plans',/\b(?:development plans?|maintenance plan|plans?|planning|roadmap|piano|piani)\b/i,9,'Plans, milestones and proposed implementation work.'],
 ['Documentation maintenance',/\b(?:documentation maintenance|docs maintenance)\b/i,8,'Commands and reference material for maintaining project documentation.'],
 ['Project Map',/\bproject map\b|\bmappa del progetto\b/i,10,'Definitions, controls and operation of the project map.'],
 ['Memory & GraphRAG',/\b(?:memory|graphrag|graph rag|retrieval|wiki|controlwork|work plane)\b/i,8,'Project memory, document retrieval and ControlWork knowledge management.'],
 ['AI agents & orchestration',/\b(?:agents?|concierge|providers?|orchestration|prompts?|local ai)\b/i,8,'Agent roles, prompts, providers and orchestration behavior.'],
 ['Setup & adoption',/\b(?:setup|installation|install|adoption|adopt|quick start|quickstart|getting started)\b/i,7,'Installing, configuring and adopting the system in a project.'],
 ['Verification & quality',/\b(?:verification|tests?|testing|review|audit|evidence|quality)\b/i,5,'Verification procedures, review and recorded evidence.'],
 ['Hooks & enforcement',/\b(?:hooks?|enforcement|security|protection)\b/i,8,'Hooks, enforcement boundaries and protection policies.'],
 ['Release & distribution',/\b(?:release|changelog|distribution|packaging)\b/i,5,'Release history, distribution and packaging contracts.'],
 ['Architecture & boundaries',/\b(?:architecture|schema|contract|boundaries|file organization|structure)\b/i,3,'System structure, interfaces, schemas and architectural boundaries.'],
 ['Desktop interface',/\b(?:panel|desktop|user interface|ui)\b/i,2,'Desktop interface configuration and operation.'],
 ['Project workflow',/\b(?:methodology|workflow|contributing|feature state|governance|cookbook|cross tool|tools reference)\b/i,3,'Working procedures, feature lifecycle and contributor guidance.']
];
function topicDescription(topic){return rules.find(r=>r[0]===topic)?.[3]||({
 'Needs classification':'No unambiguous subject is established. Review these documents before assigning a topic.',
 'Project entry points':'Repository entry documents and documentation indexes.',
 'Legal & licensing':'License, notice and trademark documents.',
 'Work conversations':'Explicitly recorded work-session summaries.'
}[topic])||`Documents explicitly assigned to "${topic}".`;}
const compare=(a,b)=>a<b?-1:a>b?1:0;
function metadata(node){
 const match=/^\uFEFF?---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(node.excerpt||'');if(!match)return {};
 const fields={};for(const line of match[1].split(/\r?\n/)){const m=/^(cc-topic|cc-series|cc-revision|cc-supersedes):\s*(.*?)\s*$/.exec(line);if(!m)continue;if(Object.hasOwn(fields,m[1]))return {};let value=m[2];if(value.startsWith('[')||value.startsWith('"')){try{value=JSON.parse(value);}catch{continue;}}else if(value.startsWith("'")&&value.endsWith("'"))value=value.slice(1,-1);fields[m[1]]=value;}
 return fields;
}
function label(value){if(typeof value!=='string'||!value.trim()||value.trim().length>80||/[\x00-\x1f\x7f]/.test(value))return null;try{encodeURIComponent(value);return value.trim();}catch{return null;}}
function topicFor(node,override){
 const assigned=label(override);if(assigned)return {topic:assigned,topicReason:`User assigned this document to "${assigned}" in this view`};
 const declared=label(metadata(node)['cc-topic']);if(declared)return {topic:declared,topicReason:`Source frontmatter declares cc-topic: ${declared}`};
 const category=label(node.category);if(category&&!['general','context','sources','notes','inbox'].includes(category.toLowerCase()))return {topic:category,topicReason:`Archive record explicitly declares category "${category}"`};
 if(node.area==='conversations')return {topic:'Work conversations',topicReason:'This source is an explicitly recorded work-session summary'};
 const filename=node.path.split('/').at(-1),normalize=s=>(s||'').replaceAll('_',' ').replaceAll('-',' '),evidence=[['title',normalize(node.title)],['filename',normalize(filename)]],matches=[];
 for(const [field,text] of evidence){for(const [topic,pattern,weight] of rules){const hit=pattern.exec(text);if(hit)matches.push({topic,weight,field,term:hit[0]});}if(matches.length)break;}
 matches.sort((a,b)=>b.weight-a.weight);const best=matches.filter(m=>m.weight===matches[0]?.weight);
 if(best.length===1){const m=best[0];return {topic:m.topic,topicReason:`Suggested: ${m.field} contains "${m.term}"; scope: ${topicDescription(m.topic)}`};}
 if(best.length>1)return {topic:'Needs classification',topicReason:`Conflicting subject evidence: ${best.map(m=>m.topic+' ('+m.field+': '+m.term+')').join('; ')}. No topic chosen automatically`};
 if(/^(?:readme|index|summary)\.md$/i.test(filename))return {topic:'Project entry points',topicReason:`The observed file ${filename} is a repository entry document or documentation index`};
 if(/^(?:license|notice|trademark)(?:[._ -].*)?\.md$/i.test(filename))return {topic:'Legal & licensing',topicReason:`The observed filename ${filename} identifies a legal or licensing document`};
 if(node.area==='plans')return {topic:'Development plans',topicReason:'Observed inside the explicitly selected plans scope'};
 return {topic:'Needs classification',topicReason:'No explicit topic, meaningful archive category or unambiguous subject term was found in the observed title/filename'};
}
function organizeKnowledge(nodes,edges,overrides={},confirmed=[]){
 const byId=new Map(nodes.map(n=>[n.id,n])),byPath=new Map(nodes.map(n=>[n.path,n.id])),meta=new Map(nodes.map(n=>[n.id,metadata(n)])),links=new Map(),invalid=new Set(),issues=[],series=new Map();
 const add=(a,b)=>{if(!byId.has(a)||!byId.has(b)||!byId.get(a).path.toLowerCase().endsWith('.md')||!byId.get(b).path.toLowerCase().endsWith('.md')){invalid.add(a);issues.push('A replacement target is outside the observed Markdown scope.');return;}if(!links.has(a))links.set(a,new Set());links.get(a).add(b);};
 for(const e of edges){if(/(?:^|: )supersedes$/.test(e.kind))add(e.source,e.target);if(/(?:^|: )superseded_by$/.test(e.kind))add(e.target,e.source);}
 for(const n of nodes){const m=meta.get(n.id),declared=m['cc-supersedes'];if(declared){const refs=Array.isArray(declared)?declared:[declared];if(refs.length>32){invalid.add(n.id);issues.push('A replacement declaration exceeds the 32-target display limit.');}for(const ref of refs.slice(0,32)){const target=typeof ref==='string'?byPath.get(ref.replace(/^\.\//,'')):null;target?add(n.id,target):(invalid.add(n.id),issues.push('A declared predecessor is missing from this observation.'));}}
  if(label(m['cc-series'])){const key=m['cc-series'].trim();if(!series.has(key))series.set(key,[]);series.get(key).push(n);}
 }
 const revision=n=>{const v=meta.get(n.id)['cc-revision'];return /^\d{1,9}$/.test(String(v))&&Number(v)>0?Number(v):null;};
 function chain(group,getRevision){const values=group.map(getRevision);if(values.some(v=>v===null)||new Set(values).size!==values.length){group.forEach(n=>invalid.add(n.id));issues.push('An ambiguous revision series remains expanded.');return;}const ordered=[...group].sort((a,b)=>getRevision(b)-getRevision(a));for(let i=1;i<ordered.length;i++)add(ordered[i-1].id,ordered[i].id);}
 for(const group of series.values())if(group.length>1)chain(group,revision);
 const possible=new Map();for(const n of nodes){if(!n.hash||series.has(meta.get(n.id)['cc-series']))continue;const match=/^(.*?)[._ -](?:v|rev|revision)[._ -]?(\d{1,9})\.md$/i.exec(n.path);if(!match)continue;const key=match[1].toLowerCase();if(!possible.has(key))possible.set(key,[]);possible.get(key).push({node:n,number:Number(match[2])});}
 const candidates=[];for(const [key,items] of possible){if(items.length<2||new Set(items.map(i=>i.number)).size!==items.length)continue;const ordered=items.sort((a,b)=>b.number-a.number),id=JSON.stringify(ordered.map(i=>[i.node.id,i.node.hash]));if(confirmed.includes(id))chain(ordered.map(i=>i.node),n=>items.find(i=>i.node.id===n.id).number);else candidates.push({id,title:key.split('/').at(-1),paths:ordered.map(i=>i.node.path)});}
 const adjacency=new Map();for(const [a,targets] of links)for(const b of targets){if(!adjacency.has(a))adjacency.set(a,new Set());if(!adjacency.has(b))adjacency.set(b,new Set());adjacency.get(a).add(b);adjacency.get(b).add(a);}
 const visited=new Set(),families={},currentById={},hidden=new Set();
 for(const start of [...adjacency.keys()].sort(compare)){if(visited.has(start))continue;const component=[],queue=[start];while(queue.length){const id=queue.pop();if(visited.has(id))continue;visited.add(id);component.push(id);queue.push(...adjacency.get(id));}
  const incoming=new Map(component.map(id=>[id,0]));for(const id of component)for(const target of links.get(id)||[])incoming.set(target,incoming.get(target)+1);
  const tips=component.filter(id=>incoming.get(id)===0),bad=component.some(id=>invalid.has(id)||(links.get(id)?.size||0)>1||incoming.get(id)>1);
  if(bad||tips.length!==1){issues.push('A branched, cyclic or conflicting replacement family remains expanded.');continue;}
  const ordered=[];let cursor=tips[0];while(cursor&&!ordered.includes(cursor)){ordered.push(cursor);cursor=[...(links.get(cursor)||[])][0];}
  if(ordered.length!==component.length){issues.push('An incomplete replacement family remains expanded.');continue;}
  families[ordered[0]]=ordered;for(const id of ordered){currentById[id]=ordered[0];if(id!==ordered[0])hidden.add(id);}
 }
 const visible=nodes.filter(n=>!hidden.has(n.id)).map(n=>{const topic=topicFor(n,overrides[n.id]),historicalPlanName=n.origin.startsWith('Repository')&&n.area==='plans'&&!n.path.startsWith('_work/plans/')&&!['Development plans','Needs classification'].includes(topic.topic);return {...n,...topic,area:historicalPlanName?(topic.topic==='Verification & quality'?'evidence':'documentation'):n.area,historyCount:(families[n.id]?.length||1)-1};});
 const ids=new Set(visible.map(n=>n.id));return {nodes:visible,edges:edges.filter(e=>ids.has(e.source)&&ids.has(e.target)),families,currentById,hiddenCount:hidden.size,issues:[...new Set(issues)],candidates};
}
module.exports={topicFor,organizeKnowledge,metadata,topicDescription,topicNames:rules.map(r=>r[0])};
