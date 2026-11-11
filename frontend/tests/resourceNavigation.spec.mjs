import {test} from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import {mkdirSync} from 'node:fs'
import {chromium} from '@playwright/test'

test('resource navigation keeps prices separate and saves existing flow and registration settings',async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true}),ctx=await browser.newContext({viewport:{width:1440,height:1000}})
 const root=path.resolve('../webui'),output=path.resolve('../.local/resource-navigation');mkdirSync(output,{recursive:true})
 const writes=[],errors=[]
 const registration={price_standard:5,price_oauth:8,max_quantity:100,registration_concurrency:5,user_registration_concurrency:2,max_open_orders:10,attempt_limit:3,resource_retry_limit:2,timeout_minutes:20,template:{mailProvider:'mailcom',preserve:'existing'},orders_open:true}
 const payment={enabled:true,subscription_flow:'cslive_go_direct_pay',max_parallel:2,per_user_parallel:1,prepare_timeout_seconds:120,quote_timeout_seconds:180,result_timeout_seconds:120,requests_per_minute:3,subscription_wait_seconds:600,renewal_check_interval_seconds:30,renewal_check_limit:10,network_restart_limit:2,activation_check_interval_seconds:5,activation_check_limit:12,post_payment_delay_seconds:5,cslive_go_visit_return:true,cslive_go_refresh_before_renewal:true,post_refresh_retries:4,post_refresh_interval_seconds:3}
 await ctx.route('**/*',route=>{
  const req=route.request(),p=new URL(req.url()).pathname
  if(p==='/'||p.startsWith('/assets/'))return route.fulfill({path:path.join(root,p==='/'?'index.html':p.slice(1))})
  if(req.method()!=='GET'){const body=req.postDataJSON();writes.push({path:p,body});if(p==='/registration/api/admin/v1/settings')Object.assign(registration,body);if(p==='/api/admin/settings/payment')Object.assign(payment,body);return route.fulfill({json:{ok:true}})}
  let json={items:[]}
  if(p==='/api/public')json={site:{name:'星桥'},features:{},announcements:[],products:[],registration_enabled:true}
  if(p==='/api/me')json={id:'admin',role:'admin',name:'测试',features:{},navigation:[]}
  if(p==='/registration/api/admin/v1/settings')json=registration
  if(p==='/api/admin/settings')json={payment,proxy:{mode:'manual'}}
  if(p==='/api/admin/orders')json={items:[],total:0}
  return route.fulfill({json})
 })
 try{
  const page=await ctx.newPage();page.on('pageerror',e=>errors.push(e.message))
  await page.goto('http://127.0.0.1:18970/#/admin/products?business=registration');await page.getByLabel('标准注册标准服务费').waitFor()
  assert.equal(await page.getByRole('heading',{name:'接单与并发'}).count(),0)
  await page.getByLabel('标准注册标准服务费').fill('6');await page.getByRole('button',{name:'保存注册价格',exact:true}).click()
  await page.getByText('注册价格已保存',{exact:true}).waitFor();assert.deepEqual(writes.at(-1).body,{price_standard:6,price_oauth:8})
  const resources=page.locator('.sidebar a').filter({hasText:/^(注册|订阅|代退)资源与调度$/});assert.deepEqual(await resources.allTextContents(),['注册资源与调度','订阅资源与调度','代退资源与调度'])
  await page.getByRole('link',{name:'注册资源与调度',exact:true}).click();await page.getByRole('heading',{name:'接单与并发'}).waitFor()
  assert.equal(await page.getByLabel('标准注册标准服务费').count(),0)
  await page.getByLabel(/^全平台同时注册/).fill('7');await page.getByRole('button',{name:'保存注册配置',exact:true}).click();await page.getByText('注册资源与调度配置已保存',{exact:true}).waitFor()
  const saved=writes.at(-1);assert.equal(saved.path,'/registration/api/admin/v1/settings');assert.equal(saved.body.registration_concurrency,7);assert.equal(saved.body.template.preserve,'existing');assert.equal('price_standard' in saved.body,false);assert.equal(registration.price_standard,6)
  for(const width of [1440,390]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await page.screenshot({path:path.join(output,`registration-${width}.png`),fullPage:true})}
  await page.setViewportSize({width:1440,height:1000});await page.getByRole('link',{name:'订阅资源与调度',exact:true}).click();await page.getByRole('link',{name:'订阅流程配置',exact:true}).click();await page.getByLabel('订阅办理流程',{exact:true}).waitFor()
  assert.equal(await page.locator('.sidebar a.active').innerText(),'订阅资源与调度');assert.equal(await page.getByLabel('订阅办理流程',{exact:true}).inputValue(),'cslive_go_direct_pay')
  await page.getByLabel('付款前网络重试次数',{exact:true}).fill('4');await page.getByRole('button',{name:'保存配置',exact:true}).click();await page.getByText('配置已保存',{exact:true}).waitFor()
  assert.equal(writes.at(-1).path,'/api/admin/settings/payment');assert.equal(writes.at(-1).body.network_restart_limit,4);assert.equal(writes.at(-1).body.subscription_flow,'cslive_go_direct_pay')
  for(const width of [1440,390]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await page.screenshot({path:path.join(output,`subscription-flow-${width}.png`),fullPage:true})}
  await page.goto('http://127.0.0.1:18970/#/admin/orders?business=subscription');await page.getByRole('heading',{name:'全部订单',exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'订阅流程配置'}).count(),0)
  assert.deepEqual(errors,[])
 }finally{await browser.close()}
})
