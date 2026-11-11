import {test} from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import {mkdirSync} from 'node:fs'
import {chromium} from '@playwright/test'
import {presetRange,todayBeijing} from '../src/lib/report-period.ts'
import {duration} from '../src/duration.ts'

test('calendar ranges and durations preserve boundaries and missing values',()=>{
 assert.deepEqual(presetRange('week','2026-11-01'),{start:'2026-10-26',end:'2026-11-01'})
 assert.deepEqual(presetRange('month','2026-01-01'),{start:'2026-01-01',end:'2026-01-01'})
 assert.deepEqual(presetRange('yesterday','2024-03-01'),{start:'2024-02-29',end:'2024-02-29'})
 assert.equal(duration(null),'未记录');assert.equal(duration(0),'0秒');assert.equal(duration(123),'2分 3秒')
})

test('account prices, custom tiers, timing, overview and disabled future menu',async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'}),context=await browser.newContext({viewport:{width:1440,height:1080}})
 context.setDefaultTimeout(12000)
 const root=path.resolve('../webui'),output=path.resolve('../.local/order-pricing-ui');mkdirSync(output,{recursive:true})
 const errors=[],requests=[],writes=[];let exclusive=true,eligible=false
 let allowMembership,markMembershipRequested;const membershipLoaded=new Promise(resolve=>allowMembership=resolve),membershipRequested=new Promise(resolve=>markMembershipRequested=resolve)
 const tier={code:'L9',name:'合作会员',threshold:0,discount:20,description:'测试会员'},membership={current:tier,automatic:tier,tiers:[tier],source:'manual',product_prices:{plus:0},service_prices:{registration_standard:0,refund:0},fee_override:null}
 const products=[{id:'plus',name:'Plus',description:'月度订阅',fee:1000,tag:'PLUS',features:[],enabled:true}]
 const nav=[['home','工作台','main'],['register','注册办理','main'],['accounts','我的账号','registration'],['subscribe','订阅办理','main'],['links','结账链接','subscription'],['renewal','续费管理','subscription'],['refund','代退办理','main'],['orders','订单中心','main'],['wallet','资金中心','main'],['activity','办理总览','main'],['membership','会员中心','tools']].map(([id,label,group])=>({id,path:'/'+id,label,group}))
 const user={id:'admin',name:'测试管理员',email:'admin@example.test',role:'admin',status:'active',balance:200000,frozen:0,features:{},membership,navigation:nav,custom_menus:[]}
 const account={...user,id:'member',name:'测试客户',email:'member@example.test',role:'member',membership_code:'L9',membership_until:null,service_prices:{registration_standard:0,registration_oauth:500,refund:300},product_prices:{plus:200},created_at:'2026-11-11T00:00:00Z',orders:3}
 const order={id:'subscription-paid',user_id:'member',email:'service@example.test',product_id:'plus',product_name:'Plus',workflow:'subscription',status:'succeeded',activation:'active',renewal:'review',fee:200,country:'PH',seats:1,source:'web',created_at:'2026-11-11T00:00:00Z',updated_at:'2026-11-11T00:04:00Z',payment:{state:'succeeded',fee_state:'charged',amount:2000,currency:'USD',last4:'4242'},timing:{payment_seconds:65,total_seconds:240,ongoing:false}}
 const refund={id:'refund-complete',email:'refund@example.test',status:'succeeded',fee:300,fee_status:'settled',created_at:order.created_at,timing:{total_seconds:123,ongoing:false}}
 const queues=Object.fromEntries(['registration','subscription','refund'].map((b,i)=>[b,{queued:1,running:2,review:1,settling:0,followup:0,accepting:true,paused:false,period_completed:i+1,period_settled:(i+1)*200,delivered:3}]))
 const overview={queues,totals:{orders:10,completed:6,partial:0,settled:1200,attention:0},attention:[],recent_orders:[],start:todayBeijing(),end:todayBeijing(),generated_at:new Date().toISOString(),trend:[]}
 await context.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname;requests.push(p+url.search)
  if(p==='/'||p.startsWith('/assets/'))return route.fulfill({path:path.join(root,p==='/'?'index.html':p.slice(1))})
  if(!p.startsWith('/api/')&&!p.startsWith('/registration/api/'))return route.fulfill({status:404,body:''})
  if(req.method()!=='GET'){writes.push({path:p,body:req.postDataJSON()});return route.fulfill({json:{ok:true}})}
  let json={items:[]}
  if(p==='/api/public')json={site:{name:'星桥',english_name:'StarBridge'},features:{},announcements:[],products,countries:[{code:'PH',name:'菲律宾',currency:'PHP'}],payment_connected:true,subscription_flow:'cslive_go_direct_pay',registration_enabled:true}
  if(p==='/api/me')json=user
  if(p==='/api/admin/users')json={items:[account],products,tiers:[tier]}
  if(p==='/api/admin/membership')json={items:[],config:{items:[tier]},products,discount_services:{registration_standard:true,registration_oauth:false,refund:true,subscription:{}},distribution:{L9:1},customers:1}
  if(p==='/api/admin/orders'||p==='/api/orders')json={items:[order],total:1,has_more:false,stats:{completed:1}}
  if(p==='/api/admin/orders/subscription-paid')json={order,payment:order.payment,trace:[],events:[],checkout:{},billing:{},closures:[]}
  if(p.endsWith('/refund/orders'))json={items:[refund],total:1,summary:{total:1,succeeded:1}}
  if(p==='/api/admin/overview')json=overview
  if(p.endsWith('/activity'))json={items:[{...order,business:'subscription',state:'completed',title:'Plus',quantity:1,delivered:0,settled:200,frozen:0,owner:'member@example.test'}],summary:{total:1,completed:1,settled:200},businesses:[{business:'subscription',total:1,active:0,settled:200}],total:1,has_more:false,show_fees:true}
  if(p==='/api/refund/quote')json={standard:500,fee:exclusive?0:100,saved:exclusive?500:400,price_source:exclusive?'account_override':'membership',membership,membership_eligible:eligible,accepting:true,executing:true}
  if(p==='/api/subscription-prices')json={rows:[],fx_updated_at:null,fx_stale:false,scheduled:true,price_interval_hours:1}
  if(p==='/api/quote')json={standard:1000,fee:exclusive?0:200,price_source:exclusive?'product_override':'membership',membership,membership_eligible:eligible,payment_connected:true}
  if(p==='/registration/api/public')json={features:{},orders_open:true,max_quantity:100,price_standard:5,price_oauth:8}
  if(p==='/registration/api/user/v1/me')json={...user,unified_account:true,points:2000}
  if(p==='/registration/api/user/v1/products')json={membership,products:[false,true].map(oauth=>({need_oauth:oauth,membership_eligible:eligible,standard_price:oauth?8:5,unit_price:exclusive?0:1,saved:exclusive?5:4,price_source:exclusive?'account_override':'membership'}))}
  if(p.endsWith('/registration-regions'))json={regions:[]}
  if(p==='/api/admin/membership'){markMembershipRequested();await membershipLoaded}
  return route.fulfill({json})
 })
 try{
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message))
  await page.goto('http://127.0.0.1:18970/#/admin/users');await page.getByRole('button',{name:'编辑',exact:true}).click()
  const dialog=page.getByRole('dialog',{name:'编辑账户'});await dialog.getByRole('heading',{name:'专属价格',exact:true}).waitFor()
  for(const name of ['注册价格','订阅套餐价格','代退价格'])assert.equal(await dialog.getByRole('heading',{name,exact:true}).count(),1)
  await dialog.getByLabel('标准账号 · 积分 / 个',{exact:true}).fill('0');await dialog.getByLabel('代退服务费 · 积分 / 账号',{exact:true}).fill('1.23')
  await page.screenshot({path:path.join(output,'account-prices-desktop.png')})
  await dialog.getByRole('button',{name:'确认保存',exact:true}).click();await dialog.waitFor({state:'hidden'})
  const saved=writes.find(r=>r.path==='/api/admin/users/member').body
  assert.equal(saved.service_prices.registration_standard,0);assert.equal(saved.service_prices.refund,123);assert.equal(saved.product_prices.plus,200)
  await page.evaluate(()=>location.hash='/admin/membership');await membershipRequested
  assert.equal(await page.getByRole('button',{name:'减免范围',exact:true}).isDisabled(),true);assert.equal(await page.locator('.membership-benefits').count(),0)
  allowMembership();await page.getByRole('button',{name:'减免范围',exact:true}).waitFor();await page.locator('.membership-distribution button').first().waitFor();await page.getByRole('button',{name:'等级规则',exact:true}).click()
  await page.getByLabel(/^等级标识/).fill('LMax');assert.equal(await page.getByLabel(/^等级标识/).evaluate(e=>e.checkValidity()),true)
  await page.getByRole('button',{name:'保存等级规则'}).click();await page.waitForFunction(()=>document.querySelector('.toast')?.textContent.includes('会员等级规则已保存'))
  assert.equal(writes.find(r=>r.path==='/api/admin/settings/tiers').body.items[0].code,'LMax')
  assert.equal(writes.find(r=>r.path==='/api/admin/settings/tiers').body.items[0].original_code,'L9')
  await page.getByRole('button',{name:'减免范围',exact:true}).click()
  assert.equal(await page.getByLabel('需要 OAuth 参与会员减免',{exact:true}).isChecked(),false)
  await page.getByLabel('Plus 参与会员减免',{exact:true}).uncheck();await page.getByRole('button',{name:'保存减免范围'}).click()
  await page.waitForTimeout(200);assert.equal(writes.find(r=>r.path==='/api/admin/settings/membership_pricing').body.subscription.plus,false)
  await page.screenshot({path:path.join(output,'membership-discounts.png'),fullPage:true})
  await page.evaluate(()=>location.hash='/admin/orders?business=subscription');await page.getByText('付款耗时',{exact:true}).waitFor()
  assert.match(await page.locator('.order-duration').innerText(),/1分 5秒/);assert.match(await page.locator('.order-duration').innerText(),/4分 0秒/)
  assert.equal(await page.locator('.badge.succeeded').filter({hasText:'订阅成功'}).count(),1)
  await page.screenshot({path:path.join(output,'subscription-orders.png'),fullPage:true})
  await page.getByRole('button',{name:'完整流程'}).click();await page.getByRole('dialog').waitFor();await page.getByText('续费处理暂未完成，不影响本次订阅结果。',{exact:true}).first().waitFor()
  await page.screenshot({path:path.join(output,'subscription-detail.png'),fullPage:true});await page.keyboard.press('Escape')
  await page.evaluate(()=>location.hash='/admin/orders?business=refund');await page.getByText('2分 3秒',{exact:false}).waitFor()
  await page.screenshot({path:path.join(output,'refund-orders.png'),fullPage:true})
  await page.evaluate(()=>location.hash='/admin');await page.locator('.dash-business-completed').first().waitFor()
  assert.equal(await page.locator('.dash-business-completed').count(),3);assert.ok(requests.some(p=>p.startsWith('/api/admin/overview?days=1')))
  await page.evaluate(()=>location.hash='/activity');await page.getByRole('button',{name:'本周',exact:true}).click();await page.waitForTimeout(150)
  const range=presetRange('week');assert.ok(requests.some(p=>p.includes('/activity?')&&p.includes('start='+range.start)&&p.includes('end='+range.end)))
  const menu=page.locator('.customer-nav-row');const labels=await menu.allTextContents();assert.equal(labels.findIndex(x=>x.includes('自动办理')),labels.findIndex(x=>x.includes('代退办理'))+1)
  assert.equal(await page.getByRole('button',{name:/自动办理/}).isDisabled(),true)
  for(const route of ['/subscribe','/refund','/register']){
   await page.evaluate(r=>location.hash=r,route);await page.getByText('专属价格',{exact:true}).waitFor()
   assert.equal(await page.getByText('专属价格',{exact:true}).count(),1)
   assert.equal(await page.getByText('此服务不参与会员减免',{exact:true}).count(),1)
   await page.screenshot({path:path.join(output,route.slice(1)+'-exclusive.png'),fullPage:true})
  }
  exclusive=false
  for(const route of ['/subscribe','/refund','/register']){
   await page.goto('http://127.0.0.1:18970/#'+route);await page.waitForTimeout(500)
   assert.equal(await page.getByText('专属价格',{exact:true}).count(),0,route)
   assert.equal(await page.getByText('此服务不参与会员减免',{exact:true}).count(),1,route)
  }
  await page.setViewportSize({width:390,height:844})
  for(const route of ['/activity','/admin/orders?business=refund','/admin','/admin/users']){
   await page.evaluate(r=>location.hash=r,route);await page.waitForTimeout(350)
   if(route==='/admin/users'){await page.getByRole('button',{name:'编辑',exact:true}).click();await dialog.waitFor()}
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,route)
   await page.screenshot({path:path.join(output,route.replace(/[^a-z]/g,'-')+'-mobile.png'),fullPage:true})
   if(route==='/admin/users')await page.keyboard.press('Escape')
  }
  assert.deepEqual(errors,[])
 }catch(e){const p=context.pages()[0];await p.screenshot({path:path.join(output,'failure.png'),fullPage:true});console.log(JSON.stringify({errors,url:p.url(),requests:requests.slice(-18),body:(await p.locator('body').innerText()).slice(-1800)}));throw e}finally{await browser.close()}
})
