// All traffic is intercepted, including writes. No production resources are used.
import {test} from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import {mkdirSync} from 'node:fs'
import {chromium} from '@playwright/test'

test('risk monitor filters, actions, settings and mobile layout',async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'})
 const root=path.resolve('../webui'),output=path.resolve('../.local/risk-monitor');mkdirSync(output,{recursive:true})
 const context=await browser.newContext({viewport:{width:1440,height:1000}});context.setDefaultTimeout(12000)
 const requests=[],errors=[],writes=[];let fail=false,resolved=false,blocked=true
 const risk={edge_per_minute:600,api_per_minute:120,order_per_minute:5,redeem_per_minute:10,inspect_per_minute:20,export_per_minute:20,login_per_minute:10,register_per_minute:5,reset_per_minute:10,slider_per_minute:30,slider_verify_per_minute:60,email_per_minute:5,email_per_day:100,email_cooldown_seconds:60,max_open_orders:10,session_hours:24,event_retention_days:90,max_topup:10000000}
 const alertSettings={enabled:true,finance_checks:true,retention_days:30,refresh_seconds:15,scan_seconds:60,behavior_window_seconds:900,login_source_accounts:10,duplicate_count:10,cancel_count:10,failure_min_count:5,failure_percent:50,queue_count:100,queue_seconds:600,processing_seconds:1200,large_adjustment:10000,rules:{query:{mode:'observe',limit:300}},trusted_proxies:[],revision:1}
 const event={id:7,category:'rate_limit',severity:'medium',user_name:'测试客户',email:'member@example.test',ip:'2001:db8::1234',detail:'业务请求超过每分钟限制，请稍后重试。',created_at:'2026-11-10T01:10:00Z',last_seen:'2026-11-10T01:12:00Z',occurrences:28,resolved:false}
 await context.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url()),p=url.pathname;requests.push(url.pathname+url.search)
  if(p==='/'||p.startsWith('/assets/'))return route.fulfill({path:path.join(root,p==='/'?'index.html':p.slice(1))})
  if(!p.startsWith('/api/')&&!p.startsWith('/registration/api/'))return route.fulfill({status:404,body:''})
  if(request.method()!=='GET'){
   writes.push({path:p,method:request.method(),body:request.postDataJSON()})
   if(p==='/api/admin/risk/7/resolve')resolved=true
   if(p.startsWith('/api/admin/blocks/'))blocked=false
   if(p==='/api/admin/blocks')blocked=true
   return route.fulfill({json:{ok:true}})
  }
  let json={items:[]}
  if(p==='/api/public')json={site:{name:'星桥',english_name:'StarBridge'},features:{},announcements:[]}
  if(p==='/api/me')json={id:'admin-fixture',name:'管理员',email:'admin@example.test',role:'admin',balance:10000000,frozen:0,features:{},custom_menus:[]}
  if(p==='/api/admin/settings')json={risk}
  if(p==='/api/admin/risk'){
   if(fail)return route.fulfill({status:503,json:{error:'模拟加载失败'}})
   const page=Number(url.searchParams.get('page')||1)
   json={items:url.searchParams.get('q')==='empty'?[]:[{...event,id:page===1?7:8,resolved}],total:url.searchParams.get('q')==='empty'?0:51,summary:{pending:3,high:1,occurrences:32,blocks:blocked?1:0},categories:['rate_limit','login_failed'],blocks:blocked?[{ip:'2001:db8::1234',reason:'连续异常请求',expires_at:'2026-11-11T01:12:00Z'}]:[],start:'2026-10-11',end:'2026-11-10',generated_at:'2026-11-10T01:12:00Z'}
  }
  if(p.endsWith('/risk/settings'))json={settings:alertSettings}
  if(p.endsWith('/risk/monitor'))json={items:[{...event,severity:'warning',category:'application',message:'注册订单排队时间较长',account:event.email,source_ip:event.ip,target:'order-fixture-123',operation:'scan',status:0,resolved_at:null}],total:1,summary:{requests:32,errors:1,blocked:0,average_ms:18},traffic:[{user_name:event.user_name,email:event.email,operation:'query',channel:'web',requests:32,errors:1,blocked:0,average_ms:18}],last_scan:event.last_seen,monitoring_enabled:true}
  return route.fulfill({json})
 })
 try{
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message))
  await page.goto('http://127.0.0.1:18970/#/admin/risk');await page.getByText('业务请求超过每分钟限制，请稍后重试。',{exact:true}).waitFor()
  assert.equal(await page.locator('.advanced-risk,.registration-native').count(),0)
  assert.equal(await page.getByRole('heading',{name:'风控监控',exact:true}).count(),1)
  await page.screenshot({path:path.join(output,'events-desktop.png'),fullPage:true})
  await page.getByRole('button',{name:'下一页',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.risk-pagination')?.textContent.includes('2 / 3'))
  assert.ok(requests.some(r=>r.includes('page=2')))
  await page.getByRole('button',{name:'上一页',exact:true}).click()
  await page.getByRole('button',{name:'详情',exact:true}).click();await page.getByRole('dialog',{name:'风险事件',exact:true}).waitFor();await page.keyboard.press('Escape')
  await page.getByRole('button',{name:'标记核查',exact:true}).click();await page.locator('.risk-status.resolved').waitFor();assert.ok(resolved)
  await page.getByLabel('搜索访问事件').fill('empty');await page.getByRole('button',{name:'查询',exact:true}).click();await page.getByText('没有符合条件的事件',{exact:true}).waitFor()
  await page.getByRole('button',{name:'重置',exact:true}).click();await page.locator('.risk-event-link').waitFor()
  fail=true;await page.getByRole('button',{name:'刷新',exact:true}).click();await page.getByRole('alert').filter({hasText:'模拟加载失败'}).waitFor();fail=false;await page.getByRole('button',{name:'重试',exact:true}).click();await page.getByRole('alert').waitFor({state:'hidden'})
  await page.getByRole('button',{name:'风控配置',exact:true}).click();const settings=page.getByRole('dialog');await settings.getByRole('heading',{name:'登录与验证',exact:true}).waitFor()
  assert.equal(await settings.locator('.risk-settings-grid input').count(),18)
  await settings.getByLabel('总请求',{exact:true}).fill('650');await settings.getByRole('button',{name:'保存配置',exact:true}).click();await settings.waitFor({state:'hidden'})
  assert.deepEqual(writes.find(w=>w.path==='/api/admin/settings/risk').body,{...risk,edge_per_minute:650})
  await page.getByRole('button',{name:/^IP 封禁/}).click();await page.getByRole('button',{name:'解除封禁',exact:true}).click();await page.getByRole('button',{name:'确认解除',exact:true}).click();await page.getByText('当前没有封禁的 IP',{exact:true}).waitFor()
  assert.ok(writes.some(w=>w.path==='/api/admin/blocks/2001%3Adb8%3A%3A1234'&&w.method==='DELETE'))
  await page.getByRole('button',{name:'封禁 IP',exact:true}).click();const block=page.getByRole('dialog',{name:'封禁 IP',exact:true});await block.getByLabel('IP 地址').fill('192.0.2.30');await block.getByLabel('封禁原因').fill('测试拦截');await block.getByRole('button',{name:'确认封禁'}).click();await block.waitFor({state:'hidden'})
  await page.getByRole('button',{name:'业务告警',exact:true}).click();await page.getByText('注册订单排队时间较长',{exact:true}).waitFor()
  await page.getByRole('button',{name:'立即检查',exact:true}).click();assert.ok(writes.some(w=>w.path.endsWith('/risk/scan')))
  await page.getByRole('button',{name:'告警设置',exact:true}).click();const alerts=page.getByRole('dialog');await alerts.getByLabel('检查间隔（秒）',{exact:true}).fill('90');await alerts.getByRole('button',{name:'保存告警设置',exact:true}).click();await alerts.waitFor({state:'hidden'})
  assert.deepEqual(writes.find(w=>w.path.endsWith('/risk/settings')).body,{...alertSettings,scan_seconds:90})
  await page.getByRole('button',{name:'详情',exact:true}).click();await page.getByText('order-fixture-123',{exact:true}).waitFor();await page.keyboard.press('Escape')
  await page.getByRole('button',{name:'访问统计',exact:true}).click();await page.locator('.risk-traffic-table tbody tr').waitFor()
  await page.setViewportSize({width:390,height:844})
  for(const tab of ['访问事件','业务告警','IP 封禁','访问统计']){
   await page.locator('.risk-navigation').getByRole('button',{name:new RegExp('^'+tab)}).click();await page.waitForTimeout(100)
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,tab)
   await page.screenshot({path:path.join(output,tab+'-mobile.png'),fullPage:true})
  }
  await page.getByRole('button',{name:'风控配置',exact:true}).click();await page.locator('.risk-settings-grid input').first().waitFor();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false)
  await page.screenshot({path:path.join(output,'settings-mobile.png'),fullPage:true})
  assert.deepEqual(errors,[])
 }finally{await browser.close()}
})
