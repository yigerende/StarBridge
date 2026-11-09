type Row = Record<string, any>

export function resolveSchema(spec:Row, value:Row={},depth=0):Row {
  if(depth>12)return value
  const referenced=value.$ref?.startsWith('#/')?value.$ref.slice(2).split('/').reduce((v:Row|undefined,k:string)=>v?.[k],spec):undefined
  const source={...referenced,...value}
  if(!source.allOf)return source
  const {allOf,...own}=source
  return [...allOf.map((part:Row)=>resolveSchema(spec,part,depth+1)),own].reduce((result:Row,part:Row)=>({
    ...result,...part,properties:{...result.properties,...part.properties},
    required:[...new Set([...(result.required||[]),...(part.required||[])])],
  }),{})
}

export function schemaFields(spec:Row,value:Row={},prefix='',depth=0):Row[] {
  if(depth>5)return []
  const s=resolveSchema(spec,value)
  const object=s.properties?s:(s.anyOf||s.oneOf||[]).map((x:Row)=>resolveSchema(spec,x)).find((x:Row)=>x.properties)||s
  if(!object.properties&&object.additionalProperties&&typeof object.additionalProperties==='object') {
    const f=resolveSchema(spec,object.additionalProperties),name=prefix+'{product_id}'
    return [{...f,name,nested:!!prefix},...schemaFields(spec,f,name+'.',depth+1)]
  }
  return Object.entries(object.properties||{}).flatMap(([key,raw])=>{
    const f=resolveSchema(spec,raw as Row),name=prefix+key
    const item=f.items?resolveSchema(spec,f.items):null
    return [{...f,name,required:object.required?.includes(key),nested:!!prefix},...schemaFields(spec,item||f,name+(item?'[].':'.'),depth+1)]
  })
}

export function fieldRules(v:Row):string {
  const rules:string[]=[]
  if(v.default!==undefined)rules.push('默认：'+JSON.stringify(v.default))
  if(v.minimum!==undefined)rules.push('最小：'+v.minimum)
  if(v.maximum!==undefined)rules.push('最大：'+v.maximum)
  if(v.minLength!==undefined)rules.push('最短 '+v.minLength+' 字符')
  if(v.maxLength!==undefined)rules.push('最长 '+v.maxLength+' 字符')
  if(v.pattern)rules.push('格式：'+v.pattern)
  if(v.format)rules.push('格式：'+v.format)
  if(v.examples?.length)rules.push('示例：'+JSON.stringify(v.examples[0]))
  return rules.join('；')
}

export function fieldType(v:Row):string {
  if(v.anyOf||v.oneOf)return (v.anyOf||v.oneOf).map(fieldType).join(' / ')
  if(v.const!==undefined)return JSON.stringify(v.const)
  if(v.enum)return v.enum.join(' / ')
  if(v.$ref)return v.$ref.split('/').at(-1)
  if(v.type==='array')return fieldType(v.items||{})+'[]'
  return Array.isArray(v.type)?v.type.join(' / '):v.type||'object'
}

export function requestExamples(x:Row):Row {
  const content=x.op.requestBody?.content?.['application/json']
  return content?.examples||(content?.example?{default:{summary:'请求示例',value:content.example}}:{})
}

export function responseExamples(response:Row):Row {
  const content=response.content?.['application/json']
  return content?.examples||(content?.example?{default:{summary:'响应示例',value:content.example}}:{})
}

export function documentedOperations(spec:Row):Row[] {
  return Object.entries(spec.paths||{}).flatMap(([path,item])=>Object.entries(item as Row)
    .filter(([method])=>['get','post','put','patch','delete','head','options'].includes(method))
    .map(([method,op])=>({path,method,op,id:(op as Row).operationId})))
}

export function codeSample(x:Row,origin:string,lang:string,exampleKey?:string):string {
  const examples=requestExamples(x),body=examples[exampleKey||Object.keys(examples)[0]]?.value
  let url=origin+x.path
  for(const p of x.op.parameters||[])if(p.in==='path')url=url.replace('{'+p.name+'}',p.example||'RECORD_ID')
  const query=(x.op.parameters||[]).filter((p:Row)=>p.in==='query'&&p.required)
  if(query.length)url+='?'+query.map((p:Row)=>p.name+'='+encodeURIComponent(p.example||'VALUE')).join('&')
  const headers:Row={Authorization:'Bearer YOUR_API_KEY'}
  for(const p of x.op.parameters||[])if(p.in==='header'&&p.required)headers[p.name]=p.example||'VALUE'
  if(body!==undefined)headers['Content-Type']='application/json'
  if(lang==='python')return `import os\nimport json\nimport requests\n\nheaders = ${JSON.stringify(headers,null,2).replace('"Bearer YOUR_API_KEY"','"Bearer " + os.environ["STARBRIDGE_API_KEY"]')}\n${body!==undefined?'payload = json.loads('+JSON.stringify(JSON.stringify(body))+')\n':''}response = requests.${x.method}(\n    ${JSON.stringify(url)}, headers=headers, ${body!==undefined?'json=payload, ':''}timeout=30\n)\nresponse.raise_for_status()\nprint(response.json())`
  if(lang==='node')return `const response = await fetch(${JSON.stringify(url)}, {\n  method: ${JSON.stringify(x.method.toUpperCase())},\n  signal: AbortSignal.timeout(30000),\n  headers: ${JSON.stringify(headers,null,2).replace('"Bearer YOUR_API_KEY"','"Bearer " + process.env.STARBRIDGE_API_KEY')}${body!==undefined?',\n  body: JSON.stringify('+JSON.stringify(body,null,2)+')':''}\n});\nconst result = await response.json();\nif (!response.ok) throw new Error(result.error || response.statusText);\nconsole.log(result);`
  const quote=(v:string)=>"'"+v.replaceAll("'","'\\''")+"'"
  return `curl --fail-with-body --connect-timeout 10 --max-time 30 ${quote(url)} \\\n  -X ${x.method.toUpperCase()} \\\n  `+Object.entries(headers).map(([k,v])=>k==='Authorization'?'-H "Authorization: Bearer $STARBRIDGE_API_KEY"':`-H ${quote(k+': '+v)}`).join(' \\\n  ')+(body!==undefined?` \\\n  --data-raw ${quote(JSON.stringify(body,null,2))}`:'')
}
