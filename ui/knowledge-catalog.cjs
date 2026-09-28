'use strict';
const {validResult}=require('./knowledge-contract.cjs');
// Replace a bounded window; never accumulate the catalog in Electron.
async function loadCatalog(call,current,selection={}){
 if(!current())throw Error('catalog_load_cancelled');
 const request={topic:null,query:'',focus:null,offset:0,snapshot:null,...selection};
 const result=await call('graph-view',request);
 if(!current())throw Error('catalog_load_cancelled');
 if(!validResult(result,'graph-view')||result.graph.offset!==request.offset||result.graph.topic!==request.topic||result.graph.query!==request.query||result.graph.focus!==request.focus||(request.snapshot&&result.graph.snapshot!==request.snapshot))throw Error('catalog_snapshot_changed');
 return result;
}
module.exports={loadCatalog};
