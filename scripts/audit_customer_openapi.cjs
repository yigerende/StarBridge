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
console.log(JSON.stringify({operations:Object.values(spec.paths).reduce((n,x)=>n+Object.keys(x).length,0),validatedExamples:examples}));
