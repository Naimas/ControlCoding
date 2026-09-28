export type Rect={x:number;y:number;w:number;h:number};
export type Camera={x:number;y:number;scale:number};
export type KnowledgeNode={id:string;title:string;path:string;area:string;state:string;origin:string;excerpt:string;hash?:string;truncated?:boolean;category?:string;topic?:string;topicReason?:string;historyCount?:number};
export type KnowledgeEdge={source:string;target:string;kind:string};
export type Tile=Rect & {id:string;title:string;count:number};
export type Cluster=Tile & {area:string;hub:Rect & {id:string}};
export type RecordChip=KnowledgeNode & Rect & {cluster:string};
export const areas:string[][];
export function layout(nodes:KnowledgeNode[],edges:KnowledgeEdge[]):{width:number;height:number;tiles:Tile[];clusters:Cluster[];records:RecordChip[];edges:{source:string;target:string;count:number}[]};
export function zoomAt(view:Camera,point:{x:number;y:number},factor:number):Camera;
export function fitRect(rect:Rect,width:number,height:number,padding?:number):Camera;
export function track(a:Rect,b:Rect):string;

export function visibleRecords(records:RecordChip[],view:Camera,size:{w:number;h:number},selected?:string):RecordChip[];
