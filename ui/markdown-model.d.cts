export type MarkdownNode={type:string;tag:string;text:string;attrs:Record<string,string>;children:MarkdownNode[]};
export function parseDocument(source:string):{tree:MarkdownNode[];headings:{id:string;label:string;level:number}[];images:string[];omittedImages:number};
