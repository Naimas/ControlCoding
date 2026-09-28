import {Rect,KnowledgeEdge,RecordChip,Cluster,Tile} from './knowledge-layout.cjs';
export type Selection={kind:'area'|'cluster'|'record';id:string}|null;
export type Endpoint=Rect&{id:string};
export type Connection={a:Endpoint;b:Endpoint;key:string;kind:string;count:number;area:string;highlight:boolean};
export function selectionScope(model:{records:RecordChip[]},edges:KnowledgeEdge[],selection:Selection):{seeds:Set<string>;related:Set<string>;clusters:Set<string>;areas:Set<string>;active:boolean};
export function routeConnections(connections:Connection[],obstacles:Endpoint[]):{routes:(Connection&{d:string;points:number[][]})[];omitted:string[]};
