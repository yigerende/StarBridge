// Local isolated preview only; every populated business record below is synthetic.
import {test} from 'node:test'
import assert from 'node:assert/strict'
import {mkdir} from 'node:fs/promises'
import {chromium} from '@playwright/test'

test('unified dashboards: useful states, deep links, cached navigation, date scope and mobile',async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_BROWSER_CHANNEL||'chrome'})
 const context=await browser.newContext({viewport:{width:1440,height:1080}}),base='http://127.0.0.1:18970'
 const stamp=new Date().toISOString(),errors=[],requests=[],durations=[]
 const queues={refund:{queued:0,running:0,review:0,followup:0,settling:0,accepting:false,paused:true},subscription:{queued:3,running:2,review:1,followup:1,settling:1,accepting:true,paused:false},registration:{queued:2,running:1,review:0,followup:0,settling:0,accepting:true,paused:true,delivered:32}}
 const recent=[{id:'delivery-one',business:'registration',title:'注册账号 · 含 OAuth',email:'',owner:'customer@example.test',status:'running',quantity:30,delivered:12,created_at:stamp,href:'#/orders?business=registration&order_id=delivery-one',claim_href:'#/accounts?business=registration&order_id=delivery-one'},
 {id:'subscription-paid',business:'subscription',title:'Plus 月度订阅',owner:'customer@example.test',email:'test@example.test',workflow:'subscription',status:'succeeded',activation:'review',renewal:'disabled',attention:true,created_at:stamp,href:'#/orders?business=subscription&order_id=subscription-paid'},
 {id:'subscription-review',business:'subscription',title:'Team 订阅',owner:'customer@example.test',email:'test2@example.test',workflow:'subscription',status:'review',attention:true,created_at:stamp,href:'#/orders?business=subscription&order_id=subscription-review'}]
 const attention=[{key:'payment_review',title:'付款结果待核实',count:1,unit:'笔',hint:'付款结果尚未确定，保留冻结；请勿再次付款。',tone:'danger',href:'#/orders?business=subscription&status=review'},
 {key:'accounts',title:'可领取账号',count:12,unit:'个',hint:'交付后 72 小时内可领取，请及时保存。',tone:'success',href:'#/accounts?business=registration'}]
 const customer={wallet:{available:123456,frozen:7800},permissions:{orders:true,register:true,registration_orders:true,accounts:true},queues,attention,recent_orders:recent,announcements:[{id:'notice',title:'账号领取提醒',body:'账号交付后请在有效期内领取，并妥善保存。'}],generated_at:stamp,active:false}
 const admin={totals:{orders:126,completed:98,partial:3,settled:258600,attention:4},queues,attention:[attention[0],{key:'followup',title:'已付款，后续待处理',count:1,unit:'笔',hint:'付款已成功，仅处理订阅生效或续费设置。',tone:'warning',href:'#/admin/orders?business=subscription&attention=followup&include_admin=false'},{key:'risk',title:'风险事件待核查',count:2,unit:'项',hint:'全站未核查事件。',tone:'warning',href:'#/admin/risk?state=pending&all_time=true'}],recent_orders:recent.map(r=>({...r,claim_href:null,href:r.href.replace('#/orders','#/admin/orders')})),start:'2026-11-04',end:'2026-11-10',include_admin:false,generated_at:stamp,active:false,trend:Array.from({length:7},(_,i)=>({day:`2026-11-${String(4+i).padStart(2,'0')}`,subscription:[10,12,5,19,11,15,8][i],registration:[8,11,6,13,22,17,10][i],subscription_fees:[12000,18000,9000,-1500,16000,18000,10000][i],registration_fees:10000+i*2000}))}
 let fail=false,empty=false,delay=0
 try{
  assert.equal((await context.request.post(base+'/api/auth/login',{data:{email:'admin',password:'admin'},headers:{Origin:base}})).status(),200)
  await context.route('**/api/overview',async r=>{requests.push(r.request().url());if(delay)await new Promise(resolve=>setTimeout(resolve,delay));return r.fulfill({status:fail?503:200,json:fail?{error:'模拟刷新失败'}:empty?{...customer,attention:[],recent_orders:[],announcements:[]}:customer})})
  await context.route('**/api/admin/overview?**',async r=>{requests.push(r.request().url());const p=new URL(r.request().url()).searchParams;return r.fulfill({json:{...admin,include_admin:p.get('include_admin')==='true',...(p.get('start')?{start:p.get('start'),end:p.get('end')}:{}),recent_orders:p.get('focus')==='attention'?admin.recent_orders.filter(o=>o.attention):admin.recent_orders}})})
  await context.route('**/registration/api/user/v1/account-orders?**',r=>r.fulfill({json:{items:[{order_id:'delivery-one',available_count:12,download_expires_at:new Date(Date.now()+86400000).toISOString(),last_delivered_at:stamp}],total:1,accounts_total:12,server_time:stamp}}))
  await context.route('**/registration/api/user/v1/registration-orders?**',r=>{requests.push(r.request().url());return r.fulfill({json:{items:[],total:0,limit:20,offset:0}})})
  await context.route('**/api/admin/orders?**',r=>{requests.push(r.request().url());return r.fulfill({json:{items:[],total:0,page:1,page_size:100}})})
  await context.route('**/api/orders?**',r=>{requests.push(r.request().url());return r.fulfill({json:{items:[],total:0}})})
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message))
  let started=performance.now();await page.goto(base+'/#/home');await page.getByText('已交付 12 / 30 个',{exact:true}).waitFor();durations.push({coldHomeMs:Math.round(performance.now()-started)})
  assert.equal(await page.locator('.business-tabs').count(),0)
  assert.equal(await page.locator('.dash-account-money strong').innerText(),'1,234.56积分')
  assert.notEqual(await page.locator('.dash-todo.danger b').evaluate(el=>getComputedStyle(el).color),'rgb(255, 255, 255)')
  assert.equal(await page.locator('.dash-order-state').filter({hasText:'已付款 · 生效待核实'}).count(),1)
  assert.equal(await page.locator('.dash-order-state').filter({hasText:'请勿再次付款'}).count(),1)
  assert.equal(requests.filter(s=>s.endsWith('/api/overview')).length,1)
  await mkdir('.local/dashboard-validation',{recursive:true})
  await page.screenshot({path:'.local/dashboard-validation/customer-desktop.png',fullPage:true})
  await page.locator('.dash-order-actions').getByRole('link',{name:'领取账号'}).click()
  await page.getByRole('heading',{name:'我的账号',exact:true}).waitFor()
  await page.getByRole('button',{name:'展开订单 delivery-one',exact:true}).waitFor()
  assert.match(page.url(),/accounts\?business=registration&order_id=delivery-one/)
  delay=1000;started=performance.now();await page.getByRole('link',{name:'工作台',exact:true}).click();await page.getByText('已交付 12 / 30 个',{exact:true}).waitFor();durations.push({cachedHomeMs:Math.round(performance.now()-started)})
  assert.ok(performance.now()-started<900,'cached view should appear before delayed request')
  await page.getByRole('button',{name:'刷新工作台'}).isEnabled().then(async enabled=>{if(!enabled)await page.waitForFunction(()=>!document.querySelector('[aria-label="刷新工作台"]').disabled)})
  delay=0;fail=true;await page.getByRole('button',{name:'刷新工作台'}).click();await page.getByRole('alert').filter({hasText:'模拟刷新失败'}).waitFor()
  assert.equal(await page.getByText('已交付 12 / 30 个',{exact:true}).count(),1)
  fail=false;await page.getByRole('button',{name:'刷新工作台'}).click();await page.getByRole('alert').waitFor({state:'hidden'})
  await page.locator('.dash-todo').filter({hasText:'付款结果待核实'}).click()
  await page.getByText('已按工作台待办筛选订单',{exact:false}).waitFor()
  assert.ok(requests.some(s=>s.includes('/api/orders?')&&s.includes('status=review')))
  await page.getByRole('tab',{name:'注册',exact:true}).click();await page.getByText('注册、授权与交付分别跟进，积分按实际交付结算。',{exact:true}).waitFor()
  assert.equal(new URLSearchParams(page.url().split('#')[1].split('?')[1]).get('status'),null)
  await page.getByRole('tab',{name:'订阅',exact:true}).click();await page.getByRole('heading',{name:'订单中心',exact:true}).waitFor()
  assert.equal(await page.getByText('已按工作台待办筛选订单',{exact:false}).count(),0)
  await page.evaluate(()=>location.hash='/admin');started=performance.now();await page.getByText('净结算积分',{exact:true}).first().waitFor();durations.push({coldAdminMs:Math.round(performance.now()-started)})
  assert.equal(await page.locator('.business-tabs').count(),0)
  assert.equal(await page.getByText('平台运行正常',{exact:true}).count(),0)
  await page.screenshot({path:'.local/dashboard-validation/admin-desktop.png',fullPage:true})
  await page.getByRole('button',{name:'净结算积分',exact:true}).click()
  assert.equal(await page.locator('.dash-chart-bars .negative').count(),1)
  await page.getByLabel('只看需关注').check();await page.waitForFunction(()=>document.querySelectorAll('.dash-order').length===2)
  await page.getByLabel('包含管理员业务').check();await page.getByText('包含管理员；待办和队列显示当前状态',{exact:false}).waitFor()
  await page.getByRole('button',{name:'自定义',exact:true}).click();await page.getByLabel('开始日期',{exact:true}).fill('2026-10-01');await page.getByLabel('结束日期',{exact:true}).fill('2026-10-03');await page.getByRole('button',{name:'应用',exact:true}).click()
  await page.getByText('2026-10-01 — 2026-10-03',{exact:false}).waitFor()
  await page.locator('.dash-attention-menu summary').click();await page.locator('.dash-todo').filter({hasText:'已付款，后续待处理'}).click();await page.getByText('当前仅显示已付款、后续待处理的订单。',{exact:false}).waitFor()
  assert.ok(requests.some(s=>s.includes('/api/admin/orders?')&&s.includes('attention=followup')))
  await page.evaluate(()=>location.hash='/admin');await page.getByText('净结算积分',{exact:true}).first().waitFor()
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:'.local/dashboard-validation/admin-mobile.png',fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false)
  await page.evaluate(()=>location.hash='/home');await page.getByText('已交付 12 / 30 个',{exact:true}).waitFor();await page.screenshot({path:'.local/dashboard-validation/customer-mobile.png',fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false)
  empty=true;await page.getByRole('button',{name:'刷新工作台'}).click();await page.getByText('还没有订单记录',{exact:true}).waitFor();await page.getByText('当前没有待处理事项，可以开始新的办理。',{exact:true}).waitFor()
  assert.deepEqual(errors,[])
  console.log(JSON.stringify({dashboardNavigation:durations,consoleErrors:errors.length}))
 }finally{await context.request.post(base+'/api/auth/logout',{headers:{Origin:base}});await browser.close()}
})
