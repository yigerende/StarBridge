import {test} from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {schemaFields,codeSample,requestExamples,responseExamples,documentedOperations,fieldRules} from '../src/apiDocs.ts'
const spec=JSON.parse(readFileSync(new URL('../../backend/openapi.json',import.meta.url),'utf8'))
const operations=Object.entries(spec.paths).flatMap(([path,v])=>Object.entries(v).map(([method,op])=>({path,method,op})))
const registration=JSON.parse(readFileSync(new URL('../../backend/registration-openapi.json',import.meta.url),'utf8'))

test('registration credentials expand inherited and nested OAuth fields once',()=>{
  const fields=schemaFields(registration,{$ref:'#/components/schemas/Credentials'})
  for(const name of ['email','password','totp_secret','access_token','session_token','oauth.access_token','download_expires_at','totp_code','totp_expires_in'])assert.ok(fields.some(f=>f.name===name),name)
  assert.equal(new Set(fields.map(f=>f.name)).size,fields.length)
  assert.ok(fields.find(f=>f.name==='totp_code').required)
})

test('all registration scenarios and multiple export responses are visible',()=>{
  for(const x of documentedOperations(registration))for(const lang of ['curl','python','node']){
    const code=codeSample({...x,path:'/registration'+x.path},'https://bridge.example.test',lang)
    assert.ok(code.includes('/registration/open/v1/'))
    assert.ok(code.includes('STARBRIDGE_API_KEY'))
    assert.ok(!code.includes('undefined'))
    assert.ok(!code.includes('{id}'))
  }
  const exported=registration.paths['/open/v1/accounts/export'].post.responses['200']
  assert.ok(Object.keys(responseExamples(exported)).length>=7)
  assert.ok(Object.values(responseExamples(exported)).every(v=>typeof v.value.filename==='string'&&typeof v.value.content==='string'))
})

test('path metadata is not mistaken for an API operation',()=>{
  const d={paths:{'/example':{summary:'Example',parameters:[],get:{operationId:'one'}}}}
  assert.deepEqual(documentedOperations(d).map(x=>x.id),['one'])
})

test('nested references, union objects and arrays expose customer fields',()=>{
  const order=schemaFields(spec,{$ref:'#/components/schemas/CreateOrder'})
  for(const field of ['session.user.email','session.account.id','session.sessionToken','card.number','card.cvc'])assert.ok(order.some(f=>f.name===field),field)
  const list=schemaFields(spec,{$ref:'#/components/schemas/OrderPage'})
  assert.ok(list.some(f=>f.name==='items[].payment.state'))
  const membership=schemaFields(spec,{$ref:'#/components/schemas/Membership'})
  assert.ok(membership.some(f=>f.name==='product_prices.{product_id}'&&f.description&&f.minimum===0))
  assert.ok(fieldRules(order.find(f=>f.name==='product_id')).includes('pro20x'))
})

test('all operations produce bounded, authenticated examples',()=>{
  for(const x of operations)for(const lang of ['curl','python','node']){
    const code=codeSample(x,'https://pay.example.test',lang)
    assert.ok(code.includes('STARBRIDGE_API_KEY'))
    assert.ok(!code.includes('{id}'))
    assert.ok(!code.includes('undefined'))
    assert.ok(code.includes(lang==='curl'?'--max-time':lang==='node'?'AbortSignal.timeout':'timeout=30'))
    if(x.op.parameters?.some(p=>p.name==='Idempotency-Key'))assert.ok(code.includes('Idempotency-Key'))
  }
})

test('link and subscription examples remain separate and all guides resolve',()=>{
  for(const guide of spec['x-guides']){
    const create=operations.find(x=>x.op.operationId===guide.create)
    assert.ok(create)
    assert.ok(requestExamples(create)[guide.example])
    assert.ok(operations.some(x=>x.op.operationId===guide.detail))
  }
  const create=operations.find(x=>x.op.operationId==='create_order')
  assert.equal(requestExamples(create).link.value.card,undefined)
  assert.equal(requestExamples(create).subscription.value.accepted,true)
  assert.equal(requestExamples(create).subscription.value.product_id,'pro20x')
  assert.ok(!codeSample(create,'https://pay.example.test','node','link').includes('YOUR_CARD_NUMBER'))
})

test('Plus and Pro Max docs offer the actual catalog IDs and both workflows',()=>{
  const create=operations.find(x=>x.op.operationId==='create_order')
  for(const id of ['plus','promax']){
    assert.ok(spec['x-product-examples'].some(p=>p.id===id))
    for(const workflow of ['link','subscription']){
      const key=id+'_'+workflow,example=requestExamples(create)[key].value
      assert.equal(example.product_id,id)
      assert.equal(example.workflow,workflow)
      assert.equal(Boolean(example.card),workflow==='subscription')
      for(const lang of ['curl','python','node'])assert.ok(codeSample(create,'https://pay.example.test',lang,key).includes(id))
    }
  }
})
