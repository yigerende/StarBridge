// Validate the merged customer contract. Replace explicit card placeholders only for schema checks; no network or payment calls.
const fs=require('fs'),assert=require('assert/strict'),{execFileSync}=require('child_process'),Ajv=require('../services/gpt-register/node_modules/ajv/dist/2020');
const spec=JSON.parse(execFileSync('python',['-c','import json;from backend.registration_docs import document;print(json.dumps(document()))'],{cwd:require('path').join(__dirname,'..'),encoding:'utf8'})),ajv=new Ajv({strict:false,validateFormats:false,allErrors:true});let examples=0;
const ids=new Set();
for(const [path,methods] of Object.entries(spec.paths))for(const op of Object.values(methods)){
 assert(/^\/(api\/v1|registration\/open\/v1)\//.test(path),'Non-customer endpoint: '+path);
 assert(!ids.has(op.operationId),'Duplicate operation: '+op.operationId);ids.add(op.operationId);
 assert.equal(op.tags.length,1,'An operation must appear in one navigation group');
 for(const part of [...Object.values(op.responses),op.requestBody].filter(Boolean)){
 const media=part.content?.['application/json'];if(!media)continue;
 let validate;try{validate=ajv.compile({...media.schema,components:spec.components})}catch(e){throw Error(path+': '+e.message)}
 for(const example of media.examples?Object.values(media.examples).map(x=>x.value):media.example?[media.example]:[]){const candidate=JSON.parse(JSON.stringify(example).replaceAll('YOUR_CARD_NUMBER','4242424242424242').replaceAll('YOUR_CVC','123'));assert(validate(candidate),path+': '+JSON.stringify(validate.errors));examples++}
 }
}
const routes=JSON.parse(execFileSync('python',['-c',"import json;from backend.main import app;print(json.dumps([{'path':r.path,'method':m.lower(),'query':[p.alias for p in r.dependant.query_params]} for r in app.routes if hasattr(r,'dependant') and r.path.startswith('/api/v1/') for m in r.methods if m not in ('HEAD','OPTIONS')]))"],{cwd:require('path').join(__dirname,'..'),encoding:'utf8'}));
const real=new Set(routes.map(r=>r.method+' '+r.path));
for(const row of routes){
 const op=spec.paths[row.path]?.[row.method];assert(op,'Missing customer operation: '+row.method+' '+row.path);
 assert.deepEqual((op.parameters||[]).filter(p=>p.in==='query').map(p=>p.name).sort(),row.query.sort(),'Query parameters differ: '+row.path);
}
for(const [path,methods] of Object.entries(spec.paths))if(path.startsWith('/api/v1/'))for(const method of Object.keys(methods))assert(real.has(method+' '+path),'Nonexistent customer operation: '+method+' '+path);
console.log(JSON.stringify({operations:Object.values(spec.paths).reduce((n,x)=>n+Object.keys(x).length,0),validatedExamples:examples,matchedBackendRoutes:real.size,queryParametersMatched:true}));
