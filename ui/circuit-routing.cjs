'use strict';
// Containment identifies seeds; only captured one-hop references expand them.
function selectionScope(model,edges,selection){
 const seeds=new Set(),related=new Set(),clusters=new Set(),areas=new Set();
 for(const n of model.records)if(selection&&(selection.kind==='record'?n.id===selection.id:selection.kind==='cluster'?n.cluster===selection.id:n.area===selection.id))seeds.add(n.id);
 for(const id of seeds)related.add(id);
 for(const e of edges){if(seeds.has(e.source))related.add(e.target);if(seeds.has(e.target))related.add(e.source);}
 for(const n of model.records)if(related.has(n.id)){clusters.add(n.cluster);areas.add(n.area);}
 if(selection?.kind==='area')areas.add(selection.id);if(selection?.kind==='cluster')clusters.add(selection.id);
 return {seeds,related,clusters,areas,active:!!selection};
}
const segment=(a,b)=>({x1:Math.min(a[0],b[0]),x2:Math.max(a[0],b[0]),y1:Math.min(a[1],b[1]),y2:Math.max(a[1],b[1]),horizontal:a[1]===b[1]});
function blocked(points,obstacles){
 for(let i=1;i<points.length;i++){const s=segment(points[i-1],points[i]);for(const r of obstacles){if(s.horizontal?s.y1>r.y+.01&&s.y1<r.y+r.h-.01&&s.x2>r.x+.01&&s.x1<r.x+r.w-.01:s.x1>r.x+.01&&s.x1<r.x+r.w-.01&&s.y2>r.y+.01&&s.y1<r.y+r.h-.01)return true;}}
 return false;
}
function sharedLength(a,b){if(a.horizontal!==b.horizontal)return 0;if(a.horizontal?Math.abs(a.y1-b.y1)>.2:Math.abs(a.x1-b.x1)>.2)return 0;return Math.max(0,a.horizontal?Math.min(a.x2,b.x2)-Math.max(a.x1,b.x1):Math.min(a.y2,b.y2)-Math.max(a.y1,b.y1));}
function rounded(points){let d=`M ${points[0].join(' ')}`;for(let i=1;i<points.length-1;i++){const a=points[i-1],b=points[i],c=points[i+1],ab=Math.hypot(b[0]-a[0],b[1]-a[1]),bc=Math.hypot(c[0]-b[0],c[1]-b[1]),r=Math.min(5,ab/2,bc/2);if(!r)continue;d+=` L ${b[0]+(a[0]-b[0])*r/ab} ${b[1]+(a[1]-b[1])*r/ab} Q ${b.join(' ')} ${b[0]+(c[0]-b[0])*r/bc} ${b[1]+(c[1]-b[1])*r/bc}`;}return d+` L ${points.at(-1).join(' ')}`;}
function clean(points){const out=[];for(const p of points){if(out.length&&p[0]===out.at(-1)[0]&&p[1]===out.at(-1)[1])continue;while(out.length>1&&((out.at(-2)[0]===out.at(-1)[0]&&out.at(-1)[0]===p[0])||(out.at(-2)[1]===out.at(-1)[1]&&out.at(-1)[1]===p[1])))out.pop();out.push(p);}return out;}
// Routes choose free rectilinear corridors; overlaps cost more than extra length.
// Dense graphs may still cross. The renderer bridges crossings with a dark casing.
function routeConnections(connections,obstacles){
 const sorted=[...connections].sort((a,b)=>Number(!!b.highlight)-Number(!!a.highlight)||a.key.localeCompare(b.key)),ports=new Map(),usage=[],routes=[],omitted=[];
 const side=(a,b)=>Math.abs((b.x+b.w/2)-(a.x+a.w/2))>Math.abs((b.y+b.h/2)-(a.y+a.h/2))?(b.x+b.w/2>a.x+a.w/2?'right':'left'):(b.y+b.h/2>a.y+a.h/2?'bottom':'top');
 for(const e of sorted)for(const [a,b] of [[e.a,e.b],[e.b,e.a]]){const key=a.id+':'+side(a,b);if(!ports.has(key))ports.set(key,[]);ports.get(key).push(e.key);}
 function port(a,b,key){const s=side(a,b),group=ports.get(a.id+':'+s),fraction=(group.indexOf(key)+1)/(group.length+1),margin=7,p=s==='left'||s==='right'?[a.x+(s==='right'?a.w:0),a.y+a.h*fraction]:[a.x+a.w*fraction,a.y+(s==='bottom'?a.h:0)];return [p,[p[0]+(s==='right'?margin:s==='left'?-margin:0),p[1]+(s==='bottom'?margin:s==='top'?-margin:0)]];}
 const xs=[...new Set(obstacles.flatMap(r=>[r.x-9,r.x+r.w+9]))],ys=[...new Set(obstacles.flatMap(r=>[r.y-9,r.y+r.h+9]))];
 for(let index=0;index<sorted.length;index++){
  const e=sorted[index];if(e.a.id===e.b.id){omitted.push(e.key);continue;}
  const [start,s]=port(e.a,e.b,e.key),[end,t]=port(e.b,e.a,e.key),near=(values,a,b)=>values.sort((x,y)=>Math.abs(x-a)+Math.abs(x-b)-Math.abs(y-a)-Math.abs(y-b)).slice(0,18);
  const laneShift=(index%5-2)*.8,xsNear=near([...xs],s[0],t[0]),ysNear=near([...ys],s[1],t[1]);
  const xLanes=[...xsNear.map(x=>x+laneShift),Math.min(...xs)-12-index*1.2,Math.max(...xs)+12+index*1.2],yLanes=[...ysNear.map(y=>y+laneShift),Math.min(...ys)-12-index*1.2,Math.max(...ys)+12+index*1.2];
  const candidates=[ [s,[t[0],s[1]],t], [s,[s[0],t[1]],t],...xLanes.map(x=>[s,[x,s[1]],[x,t[1]],t]),...yLanes.map(y=>[s,[s[0],y],[t[0],y],t]) ];
  const others=obstacles.filter(r=>r.id!==e.a.id&&r.id!==e.b.id),solids=obstacles.map(r=>({...r,x:r.x-1,y:r.y-1,w:r.w+2,h:r.h+2}));let best=null,score=Infinity;
  function inspect(candidate){const points=clean([start,...candidate,end]);if(blocked(candidate,solids)||blocked(points,others))return;const segments=points.slice(1).map((p,i)=>segment(points[i],p));let cost=points.length*8;for(const seg of segments){cost+=seg.x2-seg.x1+seg.y2-seg.y1;for(const old of usage)cost+=sharedLength(seg,old)*24;}if(cost<score){score=cost;best={...e,points,d:rounded(points),segments};}}
  candidates.forEach(inspect);
  if(!best)for(const x of xLanes.slice(0,10))for(const y of yLanes.slice(0,10)){inspect([s,[x,s[1]],[x,y],[t[0],y],t]);inspect([s,[s[0],y],[x,y],[x,t[1]],t]);}
  if(best){routes.push(best);usage.push(...best.segments);}else omitted.push(e.key);
 }
 return {routes,omitted};
}
module.exports={selectionScope,routeConnections,blocked,sharedLength};
