'use strict';
const MarkdownIt=require('./vendor/markdown-it.cjs');
const parser=new MarkdownIt({html:false,linkify:false,typographer:false,maxNesting:20});
function parseDocument(source){
 if(typeof source!=='string'||source.length>262144)throw Error('document_limit');
 const tokens=parser.parse(source,{}),headings=[],images=[],imageIds=new Set(),ids=new Set(),suffixes=new Map();let count=0;
 const slug=text=>text.toLowerCase().replace(/[^\p{L}\p{N}_\s-]/gu,'').trim().replace(/\s+/g,'-')||'section';
 function nodes(list){const roots=[],stack=[roots];
  for(let index=0;index<list.length;index++){const t=list[index];if(++count>30000)throw Error('document_limit');
   if(t.nesting===-1){stack.pop();continue;}
   const node={type:t.type,tag:t.tag,text:t.content,attrs:Object.fromEntries(t.attrs||[]),children:[]};
   if(t.type==='image'&&!imageIds.has(node.attrs.src)){imageIds.add(node.attrs.src);images.push(node.attrs.src);}
   if(t.type==='inline')node.children=nodes(t.children||[]);
   if(t.type==='heading_open'){
    const next=list[index+1];const label=(next?.children||[]).map(c=>c.type==='image'?c.content:c.content||'').join('');
    const base=slug(label);let suffix=suffixes.get(base)||0,id=suffix?base+'-'+suffix:base;
    while(ids.has(id))id=base+'-'+(++suffix);suffixes.set(base,suffix+1);ids.add(id);node.attrs.id=id;
    headings.push({id,label,level:Number(t.tag.slice(1))});
   }
   stack[stack.length-1].push(node);if(t.nesting===1)stack.push(node.children);
  }return roots;
 }
 const tree=nodes(tokens);return {tree,headings,images:images.slice(0,24),omittedImages:Math.max(0,images.length-24)};
}
module.exports={parseDocument};
