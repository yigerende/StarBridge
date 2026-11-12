import {test} from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import fs from 'node:fs'
import {chromium} from '@playwright/test'

test('resource recovery controls, paging and mother timeline',async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true}),context=await browser.newContext({viewport:{width:1440,height:1000}})
 context.setDefaultTimeout(15000)
 const root=path.resolve('../webui'),out=path.resolve('../.local/refund-resource-ui');fs.mkdirSync(out,{recursive:true})
 const errors=[],calls=[],writes=[]
 const mother={id:'mother',name:'母号 A',email:'mother@example.test',team_id:'team-fixture',proxy_id:'proxy',seat_type:'prolite',enabled:true,has_secret:true,has_refresh:true,token_expires_at:'2026-12-01T00:00:00Z'}
 const capacity={standard:{total:2,remaining:1,held:0},premium:{total:3,remaining:1,held:2},checked_at:'2026-11-12T02:00:00Z'}
 const summary={...mother,handled:20,completed:18,settled:18000,management_json:{capacity,plan:{subscription_plan:'Team',expires_at:'2026-12-10T00:00:00Z',checked_at:'2026-11-12T02:00:00Z'}}}
 const settings={auto_refresh:true}
 const resources={runtime_ready:true,resource_summary:{mothers:{total:1,enabled:1},proxies:{total:1,enabled:1}},settings:{enabled:false,max_parallel:2,max_attempts:2,retry_seconds:30,mother_interval_seconds:0,child_proxy_id:'proxy',child_proxy_backups:[],child_proxy_cooldown_seconds:300,mother_failover:{enabled:true,max_switches:3,retry_seconds:30},recovery:{enabled:true,max_retries:3,verify_intervals:[30],cleanup_intervals:[15],alert_seconds:600,cleanup_failed:true}},proxies:[{id:'proxy',name:'测试代理',target:'both',enabled:true}],mothers:[mother]}
 const restrictions=Array.from({length:12},(_,i)=>({email:'stopped'+String(i).padStart(2,'0')+'@example.test',reason_label:'上游账号已停用',first_seen_at:'2026-11-12T06:35:38Z',last_seen_at:'2026-11-12T06:40:19Z',resubmissions:2,deactivated_resubmit_limit:2,status:'blocked',cleared_at:null}))
 const members=Array.from({length:525},(_,i)=>({id:'child-'+i,email:`child${String(i).padStart(3,'0')}@example.test`,name:'成员 '+i,role:'standard-user',seat_type:'prolite',created_time:'2026-11-01T00:00:00Z',active:true,can_kick:i>0,can_leave:i>0}))
 function paging(rows,url){const p=url.searchParams,page=Number(p.get('page')||1),size=Number(p.get('page_size')||10),sort=p.get('sort_by')||'email',dir=p.get('sort_dir')||'asc',q=p.get('q')||'';rows=rows.filter(r=>JSON.stringify([r.email,r.name,r.mother_name,r.message]).includes(q));rows=[...rows].sort((a,b)=>String(a[sort]||a.id).localeCompare(String(b[sort]||b.id))*(dir==='asc'?1:-1));return {items:rows.slice((page-1)*size,page*size),total:rows.length,page,page_size:size,has_more:page*size<rows.length}}
 await context.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url()),p=url.pathname,method=request.method();calls.push([method,p,Object.fromEntries(url.searchParams)])
  if(p==='/'||p.startsWith('/assets/'))return route.fulfill({path:path.join(root,p==='/'?'index.html':p.slice(1))})
  const body=method!=='GET'?request.postDataJSON():null;if(body)writes.push({p,body})
  let json={items:[],total:0,index:[]}
  if(p==='/api/public')json={site:{name:'星桥'},features:{},products:[],countries:[],announcements:[]}
  if(p==='/api/me')json={id:'admin',name:'管理员',email:'admin@example.test',role:'admin',navigation:[],features:{}}
  if(p==='/api/admin/refund/settings'){if(body)Object.assign(resources.settings,body.settings);json=resources}
  if(p==='/api/admin/refund/resources/mothers')json=paging([mother],url)
  if(p==='/api/admin/refund/resources/proxies')json=paging(resources.proxies,url)
  if(p==='/api/admin/refund/mother-management/summary')json={items:[summary]}
  if(p==='/api/admin/refund/mother-management/settings'){if(body)Object.assign(settings,body);json=settings}
  if(p==='/api/admin/refund/mother-management/mother/resource'&&body){Object.assign(mother,body);json={ok:true}}
  if(p.endsWith('/mother/credentials'))json={access_token:'fixture-private-access',refresh_token:'fixture-private-refresh',expires_at:mother.token_expires_at}
  if(p.endsWith('/mother/members'))json=paging(members,url)
  if(p.endsWith('/mother/members/remove'))json={ok:true}
  if(p.endsWith('/logs'))json=paging([{id:1,stage:'check',created_at:'2026-11-12T00:00:00Z',message:'凭据检查完成'}],url)
  if(p==='/api/admin/refund/account-restrictions')json=paging(restrictions.filter(r=>!url.searchParams.get('status')||r.status===url.searchParams.get('status')),url)
  if(p.endsWith('/clear')&&p.includes('/account-restrictions/')){const row=restrictions.find(r=>r.email===decodeURIComponent(p.split('/').at(-2)));row.status='cleared';row.cleared_at=new Date().toISOString();row.resubmissions=0;json={ok:true}}
  if(p==='/api/admin/refund/resource-recovery')json={...paging(Array.from({length:12},(_,i)=>({id:'task'+i,refund_id:'order'+i,email:`child${String(i).padStart(2,'0')}@example.test`,mother_name:'母号 A',state:'cleaning',attempts:3,message:'成员或邀请尚未清理，继续复查',next_at:'2026-11-12T03:00:00Z'})),url),health:[{scope:'team-fixture',mother_name:'母号 A',reason:'mother_capacity',next_at:'2026-11-12T03:00:00Z'}]}
  if(p==='/api/admin/refund/orders')json={items:[{id:'order',email:'child@example.test',status:'succeeded',fee:100,fee_status:'settled',created_at:'2026-11-12T01:00:00Z',updated_at:'2026-11-12T01:00:20Z'}],total:1,summary:{succeeded:1}}
  if(p==='/api/admin/refund/orders/order')json={order:{id:'order',email:'child@example.test',status:'succeeded',fee:100,fee_status:'settled',created_at:'2026-11-12T01:00:00Z',updated_at:'2026-11-12T01:00:20Z'},events:[]}
  if(p.endsWith('/timeline'))json={active:false,generated_at:'2026-11-12T01:00:20Z',items:[{stage:'查',attempt:1,status:'已完成',started_at:'2026-11-12T01:00:01Z',finished_at:'2026-11-12T01:00:02Z',duration_seconds:1,reason:''}]}
  return route.fulfill({json})
 })
 try{
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message))
  await page.goto('http://127.0.0.1:18970/#/admin/refund')
  await page.getByRole('tab',{name:'调度与恢复',exact:true}).click()
  await page.getByLabel('允许用户再次提交次数',{exact:true}).fill('2')
  const option=page.getByLabel('查询并记录办理前个人套餐',{exact:true})
  assert.equal(await option.isChecked(),true)
  await option.uncheck();await page.getByLabel('触发保护的不同账号数',{exact:true}).fill('4');await page.getByLabel('恢复探测间隔（秒）',{exact:true}).fill('45');await page.getByRole('button',{name:'应用调度配置',exact:true}).click()
  await page.waitForFunction(()=>!document.querySelector('button.btn.primary:disabled'))
  assert.equal(writes.find(r=>r.p==='/api/admin/refund/settings').body.settings.query_personal_plan,false)
  assert.equal(writes.find(r=>r.p==='/api/admin/refund/settings').body.settings.resource_recovery.login_failure_limit,4)
  assert.equal(writes.find(r=>r.p==='/api/admin/refund/settings').body.settings.resource_recovery.login_cooldown_seconds,45)
  assert.equal(writes.find(r=>r.p==='/api/admin/refund/settings').body.settings.deactivated_resubmit_limit,2)
  const restrictionsPanel=page.locator('.account-restrictions')
  await restrictionsPanel.getByText('stopped00@example.test',{exact:true}).waitFor()
  assert.equal(await restrictionsPanel.locator('tbody tr').count(),10)
  await restrictionsPanel.getByRole('button',{name:'下一页',exact:true}).click()
  await restrictionsPanel.getByText('stopped10@example.test',{exact:true}).waitFor()
  await restrictionsPanel.getByLabel('每页条数',{exact:true}).selectOption('50')
  await restrictionsPanel.getByPlaceholder('搜索账号邮箱').fill('stopped07')
  await page.waitForFunction(()=>document.querySelectorAll('.account-restrictions tbody tr').length===1)
  await restrictionsPanel.getByRole('button',{name:'解除限制',exact:true}).click()
  await page.getByRole('dialog').waitFor()
  await page.getByRole('button',{name:'确认解除',exact:true}).click()
  await restrictionsPanel.locator('tbody').getByText('已解除',{exact:true}).waitFor()
  assert.ok(calls.some(([m,p])=>m==='POST'&&p.endsWith('/stopped07%40example.test/clear')))
  const panel=page.locator('.resource-recovery');await panel.getByText('child00@example.test',{exact:true}).waitFor()
  assert.equal(await panel.locator('tbody tr').count(),10)
  await panel.getByRole('button',{name:'下一页',exact:true}).click();await panel.getByText('child10@example.test',{exact:true}).waitFor()
  assert.equal(await panel.locator('tbody tr').count(),2)
  await panel.getByLabel('每页条数',{exact:true}).selectOption('50');await panel.getByText('child00@example.test',{exact:true}).waitFor()
  await panel.getByPlaceholder('搜索子号、母号或订单号').fill('child07');await page.waitForFunction(()=>document.querySelectorAll('.resource-recovery tbody tr').length===1)
  await panel.locator('tbody').getByRole('button',{name:'立即检查',exact:true}).click()
  assert.ok(calls.some(([m,p])=>m==='POST'&&p==='/api/admin/refund/resource-recovery/task7/retry'))
  for(const width of [1440,390]){
   await page.setViewportSize({width,height:1000});await panel.scrollIntoViewIfNeeded()
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false)
   await page.screenshot({path:path.join(out,`recovery-${width}.png`),fullPage:true})
  }
  await page.setViewportSize({width:1440,height:1000})
  await page.goto('http://127.0.0.1:18970/?business=refund#/admin/orders')
  await page.getByRole('button',{name:'查看详情',exact:true}).click()
  await page.getByText('检查母号',{exact:true}).waitFor();await page.getByText('耗时 1 秒',{exact:true}).waitFor()
  await page.screenshot({path:path.join(out,'timeline-desktop.png'),fullPage:true})
  assert.deepEqual(errors,[])
 }finally{await browser.close()}
})
