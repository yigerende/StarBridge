import {test} from 'node:test'
import assert from 'node:assert/strict'
import {readApiResponse} from '../src/apiResponse.ts'

test('JSON success and business errors preserve their values', async()=>{
 assert.deepEqual(await readApiResponse(Response.json({items:[1],total:1})),{items:[1],total:1})
 await assert.rejects(readApiResponse(Response.json({error:'母号凭据已过期，请更新 Session / AT 后重试'},{status:400})),/母号凭据已过期/)
 await assert.rejects(readApiResponse(Response.json(null,{status:500})),/HTTP 500/)
})

test('gateway HTML, invalid JSON and empty errors give a safe status message', async()=>{
 for(const status of [200,401,403,502,504]){
  await assert.rejects(readApiResponse(new Response('<!DOCTYPE html><html>private gateway diagnostic</html>',{status,headers:{'content-type':'text/html'}})),error=>{
   assert.match(error.message,new RegExp(`HTTP ${status}`))
   assert.doesNotMatch(error.message,/Unexpected|DOCTYPE|private|html/)
   return true
  })
 }
 await assert.rejects(readApiResponse(new Response('',{status:503})),/HTTP 503/)
 await assert.rejects(readApiResponse(new Response('{',{status:200})),/数据格式异常/)
})

test('request cancellation remains an AbortError',async()=>{
 const reason=new DOMException('The operation was aborted','AbortError')
 await assert.rejects(readApiResponse({json:async()=>{throw reason}}),error=>error===reason)
})
