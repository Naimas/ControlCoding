'use strict';
const areas=[['documentation','Documentation'],['conversations','Conversations'],['plans','Plans'],['decisions','Decisions'],['archive','Archive & handoffs'],['evidence','Evidence & context']];
const compare=(a,b)=>a<b?-1:a>b?1:0;
// Topic hubs use concentric radial rings. Uniform scaling keeps the raw spacing
// (400 units per ring, 240x104 document rectangles) collision-free at every zoom.
function layout(nodes,edges){
 const tiles=[],clusters=[],records=[],membership=new Map();
 areas.forEach(([id,title],index)=>{
  const tile={id,title,x:70+(index%3)*1390,y:280+Math.floor(index/3)*1110,w:1220,h:940,count:0};
  const members=nodes.filter(n=>n.area===id).sort((a,b)=>compare(a.path,b.path)||compare(a.id,b.id));tile.count=members.length;tiles.push(tile);
  const groups=new Map();for(const n of members){const topic=n.topic||require('./knowledge-topics.cjs').topicFor(n).topic;if(!groups.has(topic))groups.set(topic,[]);groups.get(topic).push(n);}
  const topics=[...groups.keys()].sort(compare),cols=Math.max(1,Math.ceil(Math.sqrt(topics.length*1.35))),rows=Math.max(1,Math.ceil(topics.length/cols)),cw=1120/cols,ch=680/rows;
  topics.forEach((topic,i)=>{
   const group=groups.get(topic),cluster={id:'topic:'+id+':'+encodeURIComponent(topic),area:id,title:topic,x:tile.x+50+(i%cols)*cw,y:tile.y+205+Math.floor(i/cols)*ch,w:cw-22,h:ch-24,count:group.length};
   let rings=1;while(4*rings*(rings+1)<group.length)rings++;
   const extent=rings*400,scale=Math.min((cluster.w-12)/(2*extent+280),(cluster.h-12)/(2*extent+160)),cx=cluster.x+cluster.w/2,cy=cluster.y+cluster.h/2;
   cluster.hub={id:cluster.id,x:cx-120*scale,y:cy-50*scale,w:240*scale,h:100*scale};clusters.push(cluster);
   let cursor=0;for(let ring=1;ring<=rings;ring++){const count=Math.min(ring*8,group.length-cursor);for(let j=0;j<count;j++){
    const n=group[cursor++],angle=-Math.PI/2+j*2*Math.PI/count,radius=ring*400*scale,w=240*scale,h=104*scale;
    const rect={...n,topic,cluster:cluster.id,x:cx+Math.cos(angle)*radius-w/2,y:cy+Math.sin(angle)*radius-h/2,w,h};records.push(rect);membership.set(n.id,cluster.id);
   }}
  });
 });
 const aggregated=new Map();for(const e of edges){const a=membership.get(e.source),b=membership.get(e.target);if(!a||!b||a===b)continue;const key=a+'\0'+b;const old=aggregated.get(key);if(old)old.count++;else aggregated.set(key,{source:a,target:b,count:1});}
 return {width:4160,height:2420,tiles,clusters,records,edges:[...aggregated.values()].sort((a,b)=>compare(a.source,b.source)||compare(a.target,b.target))};
}
function zoomAt(view,point,factor){const scale=Math.max(.06,Math.min(128,view.scale*factor)),ratio=scale/view.scale;return {scale,x:point.x-(point.x-view.x)*ratio,y:point.y-(point.y-view.y)*ratio};}
function fitRect(rect,width,height,padding=45){const scale=Math.max(.06,Math.min(64,(width-padding*2)/rect.w,(height-padding*2)/rect.h));return {scale,x:width/2-(rect.x+rect.w/2)*scale,y:height/2-(rect.y+rect.h/2)*scale};}
function rounded(points){let out=`M ${points[0].join(' ')}`;for(let i=1;i<points.length-1;i++){const a=points[i-1],b=points[i],c=points[i+1],ab=Math.hypot(b[0]-a[0],b[1]-a[1]),bc=Math.hypot(c[0]-b[0],c[1]-b[1]);if(!ab||!bc)continue;const r=Math.min(22,ab/2,bc/2);out+=` L ${b[0]+(a[0]-b[0])*r/ab} ${b[1]+(a[1]-b[1])*r/ab} Q ${b.join(' ')} ${b[0]+(c[0]-b[0])*r/bc} ${b[1]+(c[1]-b[1])*r/bc}`;}return out+` L ${points.at(-1).join(' ')}`;}
function track(a,b){const start=[a.x+a.w,a.y+a.h/2],end=[b.x,b.y+b.h/2];if(b.x>a.x+a.w+30){const mid=(start[0]+end[0])/2;return rounded([start,[mid,start[1]],[mid,end[1]],end]);}const rail=Math.min(a.y,b.y)-28;return rounded([start,[start[0]+18,start[1]],[start[0]+18,rail],[end[0]-18,rail],[end[0]-18,end[1]],end]);}
function visibleRecords(records,view,size,selected=''){
 if(records.length<=300)return records;
 const margin=48/view.scale,left=(-view.x)/view.scale-margin,top=(-view.y)/view.scale-margin,right=(size.w-view.x)/view.scale+margin,bottom=(size.h-view.y)/view.scale+margin;
 const pinned=records.find(n=>n.id===selected),found=records.filter(n=>n.id!==selected&&n.x+n.w>=left&&n.x<=right&&n.y+n.h>=top&&n.y<=bottom&&n.w*view.scale>=8);
 return [...(pinned?[pinned]:[]),...found.slice(0,pinned?255:256)];
}
module.exports={areas,layout,zoomAt,fitRect,track,visibleRecords};
