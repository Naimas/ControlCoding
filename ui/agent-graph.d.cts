export type GraphNode={id:string;label:string;x:number;y:number;status:string;detail:string;dim:boolean;role?:string;reasons?:string[];mode?:string;provider?:string;model?:string;context?:string;used?:number;limit?:number};
export type GraphEdge={id:string;from:string;to:string;label:string;status:string;role:string|null;detail:string;rail:number|null;dim:boolean};
export function projectGraph(data:any,options?:{planned?:boolean;focus?:string}):{nodes:GraphNode[];edges:GraphEdge[];height:number;labels:Record<string,string>};
