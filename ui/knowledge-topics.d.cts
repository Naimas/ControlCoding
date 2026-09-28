import {KnowledgeNode,KnowledgeEdge} from './knowledge-layout.cjs';
export const topicNames:string[];
export function topicDescription(topic:string):string;
export function topicFor(node:KnowledgeNode,override?:string):{topic:string;topicReason:string};
export function organizeKnowledge(nodes:KnowledgeNode[],edges:KnowledgeEdge[],overrides?:Record<string,string>,confirmed?:string[]):{nodes:KnowledgeNode[];edges:KnowledgeEdge[];families:Record<string,string[]>;currentById:Record<string,string>;hiddenCount:number;issues:string[];candidates:{id:string;title:string;paths:string[]}[]};
