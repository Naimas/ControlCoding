/* Trusted standalone parser. Input is text, never an executable project path. */
'use strict';
const {isMainThread,Worker,parentPort,workerData}=require('node:worker_threads');
const {createHash}=require('node:crypto');
const MAX_INPUT=12*1024*1024,MAX_OUTPUT=1024*1024;
const id=/^[A-Za-z_$][A-Za-z0-9_$]*$/;
function analyze(file){
 const parser=require('@babel/parser');
 let tree;
 try {tree=parser.parse(file.text,{sourceType:'unambiguous',attachComment:false,errorRecovery:false,
  plugins:[...(/\.[cm]?tsx?$/i.test(file.path)?['typescript']:[]),...(/\.[jt]sx$/i.test(file.path)?['jsx']:[])]});}
 catch {return {state:'parse_unavailable',symbols:[],imports:[]};}
 const symbols=[],imports=[],counts=new Map(),stack=[{node:tree,parent:null,fn:null}],functions=new Map();let count=0;
 const children=n=>Object.entries(n).filter(([k])=>!['loc','extra','comments','tokens','errors'].includes(k)).flatMap(([,v])=>Array.isArray(v)?v:[v]).filter(v=>v&&typeof v==='object'&&typeof v.type==='string');
 const branches=new Set(['IfStatement','ForStatement','ForInStatement','ForOfStatement','WhileStatement','DoWhileStatement','CatchClause','ConditionalExpression']);
 while(stack.length){
  const item=stack.pop(),n=item.node;if(++count>20000)throw Error('parse_limit');
  let parent=item.parent,fn=item.fn;
  const isFunction=['FunctionDeclaration','FunctionExpression','ArrowFunctionExpression','ObjectMethod','ClassMethod','ClassPrivateMethod','TSDeclareFunction'].includes(n.type);
  const isClass=['ClassDeclaration','ClassExpression'].includes(n.type);
  if(isFunction||isClass){
   const raw=n.id?.name||(!n.computed?n.key?.name:null),name=raw&&id.test(raw)?raw:(isClass?'anonymous class':'anonymous function');
   const qualified=(parent?symbols.find(s=>s.key===parent)?.title+'.':'')+name;
   const occurrence=(counts.get(qualified)||0)+1;counts.set(qualified,occurrence);
   const row={key:qualified+':'+occurrence,title:qualified.slice(0,160),kind:isClass?'class':'function',line:n.loc.start.line,end_line:n.loc.end.line,parent,branches:isFunction?1:0,duplicate:null};
   symbols.push(row);parent=row.key;fn=isFunction?row:null;
   if(isFunction&&n.body?.type==='BlockStatement'&&row.end_line-row.line+1>=8&&n.body.body.length>=5){
    // Structural equality ignores locations/comments and the outer function name.
    // Identifiers and literal values stay significant, but only the hash leaves here.
    const normalized=JSON.stringify({params:n.params,body:n.body},(k,v)=>['loc','start','end','extra','leadingComments','innerComments','trailingComments'].includes(k)?undefined:v);
    row.duplicate=createHash('sha256').update(normalized).digest('hex');
   }
   if(isFunction)functions.set(n,row);
  } else if(fn&&(branches.has(n.type)||(n.type==='SwitchCase'&&n.test)||(n.type==='LogicalExpression'&&['&&','||','??'].includes(n.operator))))fn.branches++;
  if(['ImportDeclaration','ExportNamedDeclaration','ExportAllDeclaration'].includes(n.type)&&n.source){
   imports.push({module:n.source.value,level:0,names:[],line:n.loc.start.line,dynamic:false});
  } else if(n.type==='TSImportEqualsDeclaration'&&n.moduleReference?.type==='TSExternalModuleReference'){
   imports.push({module:n.moduleReference.expression?.value||'',level:0,names:[],line:n.loc.start.line,dynamic:false});
  } else if(n.type==='ImportExpression'||n.type==='CallExpression'&&(n.callee?.type==='Import'||n.callee?.name==='require')){
   imports.push({module:'',level:0,names:[],line:n.loc.start.line,dynamic:true});
  }
  for(const child of children(n).reverse())stack.push({node:child,parent,fn});
 }
 if(symbols.length>512||imports.length>512)throw Error('parse_limit');
 return {state:'parsed',symbols,imports,ast_nodes:count};
}
if(!isMainThread){
 try {parentPort.postMessage({schema_version:1,parser:'babel-7.29.7',files:workerData.files.map(f=>({path:f.path,...analyze(f)}))});}
 catch {parentPort.postMessage({error:'parse_limit'});}
} else {
 let size=0,chunks=[],finished=false,worker;
 const finish=result=>{if(finished)return;finished=true;clearTimeout(deadline);worker?.terminate();const value=JSON.stringify(result);process.stdout.end(Buffer.byteLength(value)<=MAX_OUTPUT?value:JSON.stringify({error:'output_limit'}),()=>{process.stdin.destroy();process.exit(0);});};
 // Remains responsive even if parsing blocks, or the owning Python helper exits.
 const deadline=setTimeout(()=>finish({error:'parser_timeout'}),4500);
 process.stdout.on('error',()=>process.exit(1));process.stdin.on('error',()=>finish({error:'parser_input'}));
 process.stdin.on('data',data=>{size+=data.length;if(size>MAX_INPUT){finish({error:'input_limit'});process.stdin.destroy();}else chunks.push(data);});
 process.stdin.on('end',()=>{
  if(finished)return;
  try {
   const payload=JSON.parse(Buffer.concat(chunks).toString('utf8'));chunks=[];
   if(!Array.isArray(payload.files)||payload.files.length>128||payload.files.some(f=>typeof f.path!=='string'||typeof f.text!=='string'||Buffer.byteLength(f.text)>1048576))throw Error();
   worker=new Worker(__filename,{workerData:payload,resourceLimits:{maxOldGenerationSizeMb:128,stackSizeMb:8}});
   worker.once('message',finish);worker.once('error',()=>finish({error:'parser_unavailable'}));worker.once('exit',code=>{if(!finished)finish({error:'parser_unavailable'});});
  }catch{finish({error:'parser_input'});}
 });
}
