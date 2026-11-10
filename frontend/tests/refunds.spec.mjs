// Uses the local isolated preview. Every submission and displayed order is mocked.
import {test} from 'node:test'
import assert from 'node:assert/strict'
import {mkdir} from 'node:fs/promises'
import {chromium} from '@playwright/test'

test('refund forms, retry key, unified navigation, resources, documentation and mobile',async()=>{
 const b=await chromium.launch({channel:'chrome',headless:true}),c=await b.newContext({viewport:{width:1440,height:1000}}),base='http://127.0.0.1:18970',errors=[],submissions=[],timings=[]
 try{
  assert.equal((await c.request.post(base+'/api/auth/login',{data:{email:'admin',password:'admin'}})).status(),200)
  const settings=await (await c.request.get(base+'/api/admin/refund/settings')).json()
  const quote=await (await c.request.get(base+'/api/refund/quote')).json()
  const order={id:'offline-ui-fixture',email:'authorized@fixture.invalid',fee:500,status:'queued',fee_status:'frozen',message:'申请已受理',created_at:new Date().toISOString(),updated_at:new Date().toISOString()}
  let attempt=0
  await c.route('**/api/refund/quote',r=>r.fulfill({json:{...quote,fee:500,standard:1000,saved:500,accepting:true,executing:true}}))
  await c.route('**/api/refund/orders**',async r=>{
   const path=new URL(r.request().url()).pathname
   if(r.request().method()==='POST'&&path.endsWith('/orders')){submissions.push(r.request().postDataJSON());attempt++;await r.fulfill({status:attempt===1?503:200,json:attempt===1?{error:'模拟响应暂未确认，请用原请求标识重试'}:{ids:[order.id],replayed:false}})}
   else if(path.endsWith('/cancel')){order.status='cancelled';order.fee_status='released';order.message='申请已取消，服务费已释放';await r.fulfill({json:{ok:true}})}
   else if(path.endsWith('/'+order.id))await r.fulfill({json:{order,events:[{id:1,message:order.message,created_at:order.created_at}]}})
   else await r.fulfill({json:{items:[order],total:1,page:1,page_size:25}})
  })
  await c.route('**/api/admin/refund/settings',r=>r.fulfill({json:settings}))
  const p=await c.newPage();p.on('pageerror',e=>errors.push(e.message))
  let t=performance.now();await p.goto(base+'/#/refund');await p.getByRole('heading',{name:'本次服务费'}).waitFor();timings.push({coldApplyMs:Math.round(performance.now()-t)})
  assert.equal(await p.locator('.business-tabs').count(),0)
  await p.getByLabel('账号邮箱',{exact:true}).fill(order.email);await p.getByLabel('账号密码',{exact:true}).fill('offline-only-password');await p.getByLabel('2FA 密钥',{exact:true}).fill('JBSWY3DPEHPK3PXP')
  await p.getByRole('checkbox').check();await p.getByRole('button',{name:'确认提交'}).click();await p.getByRole('alert').filter({hasText:'模拟响应暂未确认'}).waitFor()
  await p.getByRole('button',{name:'确认提交'}).click();await p.getByText('已受理 1 笔申请',{exact:true}).waitFor();assert.equal(submissions[0].idempotency_key,submissions[1].idempotency_key)
  assert.equal(await p.getByLabel('账号密码',{exact:true}).inputValue(),'');assert.equal(await p.getByLabel('2FA 密钥',{exact:true}).inputValue(),'')
  await p.getByRole('link',{name:'查看进度',exact:true}).click();await p.getByRole('dialog').waitFor();await p.getByRole('checkbox',{name:'确认取消'}).check();await p.getByRole('button',{name:'取消申请',exact:true}).click();await p.getByText('申请已取消，服务费已释放',{exact:true}).first().waitFor();await p.getByRole('button',{name:'关闭',exact:true}).click()
  assert.equal(await p.getByRole('tab',{name:'代退',exact:true}).getAttribute('aria-selected'),'true')
  await p.getByRole('link',{name:'资金中心',exact:true}).click();await p.getByRole('heading',{name:'资金中心',exact:true}).waitFor();await p.getByRole('tab',{name:'代退',exact:true}).click();await p.getByRole('heading',{name:'代退资金明细'}).waitFor()
  for(let i=0;i<3;i++){t=performance.now();await p.getByRole('link',{name:'代退办理',exact:true}).click();await p.getByRole('heading',{name:'本次服务费'}).waitFor();timings.push({warmApplyMs:Math.round(performance.now()-t)});await p.getByRole('link',{name:'订单中心',exact:true}).click();await p.getByRole('heading',{name:'订单中心',exact:true}).waitFor()}
  await p.goto(base+'/#/refund');await p.getByRole('button',{name:'批量办理',exact:true}).click();await p.getByLabel('账号资料').fill('email,password,totp_secret\nfirst@fixture.invalid,"a,b",JBSWY3DPEHPK3PXP\nsecond@fixture.invalid,pw,JBSWY3DPEHPK3PXP');await p.getByText('已识别 2 个账号',{exact:false}).waitFor()
  await mkdir('.local/refund-integration',{recursive:true});await p.screenshot({path:'.local/refund-integration/apply-batch.png',fullPage:true})
  await p.goto(base+'/#/admin/refund');await p.getByRole('heading',{name:'代退资源与调度',exact:true}).waitFor();await p.getByRole('button',{name:'录入母号',exact:true}).click();await p.getByRole('dialog').waitFor();await p.getByLabel('母号专属代理').waitFor();await p.getByRole('button',{name:'关闭',exact:true}).click()
  await p.getByRole('tab',{name:'代退代理',exact:true}).click();await p.getByRole('button',{name:'录入代理',exact:true}).click();await p.getByLabel('代理地址').waitFor();await p.getByRole('button',{name:'关闭',exact:true}).click()
  await p.getByRole('tab',{name:'调度与恢复',exact:true}).click();for(const label of ['并行母号上限','同母号接单间隔（秒）','自动重试次数（首次失败之后）','最多重新办理轮次','每轮核验尝试上限'])await p.getByLabel(label,{exact:true}).waitFor()
  await p.getByRole('tab',{name:'母号资源',exact:true}).click();await p.screenshot({path:'.local/refund-integration/resources-desktop.png',fullPage:true})
  const adminOrder={...order,id:'offline-admin',status:'attention',fee_status:'frozen'},recovery={running:false,needs_review:true,can_release:false},actions=[]
  await c.route('**/api/admin/refund/orders**',async r=>{
   const path=new URL(r.request().url()).pathname
   if(path.endsWith('/resume')){actions.push(r.request().postDataJSON());recovery.running=true;await r.fulfill({json:{ok:true}})}
   else if(path.endsWith('/timeline'))await r.fulfill({json:{items:[{stage:'登',attempt:2,status:'已完成',started_at:order.created_at,finished_at:order.updated_at,duration_seconds:3,reason:'',personal_plan:{status:'observed',label:'个人 Plus'}},{stage:'核',attempt:1,status:'待核实',started_at:order.created_at,finished_at:null,duration_seconds:null,reason:'等待核实'}],active:true,generated_at:order.created_at,recovery:{started_at:order.created_at,label:'等待人工核实',reason:'结果待核实',pending:false,running:recovery.running,next_at:null,retries:2,checks:1,cleanups:0,switches:0,fallbacks:0}}})
   else if(path.endsWith('/'+adminOrder.id))await r.fulfill({json:{order:adminOrder,events:[],recovery}})
   else await r.fulfill({json:{items:[{...adminOrder,user_email:'fixture@local.test'}],total:1,summary:{attention:1}}})
  })
  await p.goto(base+'/?business=refund#/admin/orders');await p.getByRole('heading',{name:'订单中心',exact:true}).waitFor();await p.getByRole('button',{name:'查看详情',exact:true}).click()
  await p.getByText('个人 Plus',{exact:true}).waitFor();assert.equal(await p.getByLabel('处置方式').inputValue(),'verify_only')
  assert.equal(await p.getByLabel('处置方式').locator('option').count(),3)
  await p.screenshot({path:'.local/refund-integration/admin-timeline.png',fullPage:true})
  await p.getByPlaceholder('填写核实依据或原因，不包含账号凭据').fill('离线测试已核实，需要只读查询结果')
  await p.getByRole('checkbox',{name:'我已核实当前状态及操作依据'}).check();const resumed=p.waitForResponse(r=>r.url().endsWith('/resume'));await p.getByRole('button',{name:'执行处置',exact:true}).click();await resumed
  await p.getByRole('checkbox',{name:'我已核实当前状态及操作依据'}).waitFor();assert.equal(actions[0].action,'verify_only')
  await p.getByRole('button',{name:'关闭',exact:true}).click()
  await p.setViewportSize({width:390,height:844})
  for(const route of ['/refund','/orders?business=refund','/wallet','/admin/refund','/admin/products?business=refund','/admin/finance']){await p.goto(base+'/#'+route);await p.locator('h1,h2').first().waitFor();await p.waitForTimeout(180);assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'mobile overflow '+route)}
  await p.goto(base+'/#/refund');await p.getByRole('heading',{name:'本次服务费'}).waitFor();await p.screenshot({path:'.local/refund-integration/apply-mobile.png',fullPage:true})
  const spec=await (await c.request.get(base+'/api/openapi.json')).json();const ops=Object.entries(spec.paths).filter(([k])=>k.startsWith('/api/v1/refund/')).flatMap(([,v])=>Object.values(v));assert.equal(ops.length,5);assert.equal(new Set(ops.map(v=>v.operationId)).size,5)
  assert.deepEqual(errors,[]);console.log(JSON.stringify({timings,mobileWidth:390,browserErrors:errors.length}))
 }finally{await b.close()}
})
