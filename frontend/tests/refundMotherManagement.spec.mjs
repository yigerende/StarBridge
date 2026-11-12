import {test} from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import fs from 'node:fs'
import {chromium} from '@playwright/test'

test('mother resources, credentials, members and inline controls',async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true}),context=await browser.newContext({viewport:{width:1440,height:1000}})
 context.setDefaultTimeout(15000)
 const root=path.resolve('../webui'),out=path.resolve('../.local/mother-management');fs.mkdirSync(out,{recursive:true})
 const errors=[],calls=[],writes=[]
 const mother={id:'mother',name:'母号 A',email:'mother@example.test',team_id:'team-fixture',proxy_id:'proxy',seat_type:'prolite',enabled:true,has_secret:true,has_refresh:true,token_expires_at:'2026-12-01T00:00:00Z'}
 const capacity={standard:{total:2,remaining:1,held:0},premium:{total:3,remaining:1,held:2},checked_at:'2026-11-12T02:00:00Z'}
 const summary={...mother,handled:20,completed:18,settled:18000,management_json:{capacity,plan:{subscription_plan:'Team',expires_at:'2026-12-10T00:00:00Z',checked_at:'2026-11-12T02:00:00Z'}}}
 const settings={auto_refresh:true}
 const resources={runtime_ready:true,resource_summary:{mothers:{total:1,enabled:1},proxies:{total:1,enabled:1}},settings:{enabled:false,max_parallel:2,max_attempts:2,retry_seconds:30,mother_interval_seconds:0,child_proxy_id:'proxy',child_proxy_backups:[],child_proxy_cooldown_seconds:300,mother_failover:{enabled:true,max_switches:3,retry_seconds:30},recovery:{enabled:true,max_retries:3,verify_intervals:[30],cleanup_intervals:[15],alert_seconds:600,cleanup_failed:true}},proxies:[{id:'proxy',name:'测试代理',target:'both',enabled:true}],mothers:[mother]}
 const members=Array.from({length:525},(_,i)=>({id:'child-'+i,email:`child${String(i).padStart(3,'0')}@example.test`,name:'成员 '+i,role:'standard-user',seat_type:'prolite',created_time:'2026-11-01T00:00:00Z',active:true,can_kick:i>0,can_leave:i>0}))
 function paging(rows,url){const p=url.searchParams,page=Number(p.get('page')||1),size=Number(p.get('page_size')||10),sort=p.get('sort_by')||'email',dir=p.get('sort_dir')||'asc',q=p.get('q')||'';rows=rows.filter(r=>JSON.stringify([r.email,r.name,r.mother_name,r.message]).includes(q));rows=[...rows].sort((a,b)=>String(a[sort]||a.id).localeCompare(String(b[sort]||b.id))*(dir==='asc'?1:-1));return {items:rows.slice((page-1)*size,page*size),total:rows.length,page,page_size:size,has_more:page*size<rows.length}}
 await context.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url()),p=url.pathname,method=request.method();calls.push([method,p,Object.fromEntries(url.searchParams)])
  if(p==='/'||p.startsWith('/assets/'))return route.fulfill({path:path.join(root,p==='/'?'index.html':p.slice(1))})
  const body=method!=='GET'?request.postDataJSON():null;if(body)writes.push({p,body})
  let json={items:[],total:0,index:[]}
  if(p==='/api/public')json={site:{name:'星桥'},features:{},products:[],countries:[],announcements:[]}
  if(p==='/api/me')json={id:'admin',name:'管理员',email:'admin@example.test',role:'admin',navigation:[],features:{}}
  if(p==='/api/admin/refund/settings')json=resources
  if(p==='/api/admin/refund/resources/mothers')json=paging([mother],url)
  if(p==='/api/admin/refund/resources/proxies')json=paging(resources.proxies,url)
  if(p==='/api/admin/refund/mother-management/summary')json={items:[summary]}
  if(p==='/api/admin/refund/mother-management/settings'){if(body)Object.assign(settings,body);json=settings}
  if(p==='/api/admin/refund/mother-management/mother/resource'&&body){Object.assign(mother,body);json={ok:true}}
  if(p.endsWith('/mother/credentials'))json={access_token:'fixture-private-access',refresh_token:'fixture-private-refresh',expires_at:mother.token_expires_at}
  if(p.endsWith('/mother/members'))json=paging(members,url)
  if(p.endsWith('/mother/members/remove'))json={ok:true}
  if(p.endsWith('/logs'))json=paging([{id:1,stage:'check',created_at:'2026-11-12T00:00:00Z',message:'凭据检查完成'}],url)
  return route.fulfill({json})
 })
 try{
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message))
  await page.goto('http://127.0.0.1:18970/#/admin/refund')
  const section=page.locator('#refund-resource-mothers')
  await section.getByRole('button',{name:'套餐 / 成员 / 凭据',exact:true}).click()
  let dialog=page.getByRole('dialog');await dialog.getByText('180.00 积分',{exact:true}).waitFor()
  await dialog.getByRole('button',{name:'查看 AT / RT',exact:true}).click();await dialog.locator('textarea').first().waitFor();assert.equal(await dialog.locator('textarea').first().inputValue(),'fixture-private-access')
  assert.ok(!(await page.evaluate(()=>JSON.stringify(localStorage))).includes('fixture-private'))
  await dialog.getByRole('button',{name:'团队成员',exact:true}).click();await dialog.getByText('child000@example.test',{exact:true}).waitFor()
  assert.equal(await dialog.locator('tbody tr').count(),10)
  assert.equal(await dialog.getByRole('button',{name:'母号移出',exact:true}).first().isEnabled(),false)
  await dialog.getByLabel('每页条数',{exact:true}).selectOption('500');await dialog.locator('tbody tr').nth(499).waitFor()
  await dialog.getByRole('button',{name:'下一页',exact:true}).click();await dialog.getByText('child500@example.test',{exact:true}).waitFor();assert.equal(await dialog.locator('tbody tr').count(),25)
  await dialog.getByRole('button',{name:'成员，降序排序',exact:true}).click();await dialog.locator('tbody tr').first().getByText('child524@example.test',{exact:true}).waitFor();assert.equal(await dialog.locator('tbody tr').first().locator('td').first().innerText(),'child524@example.test\n成员 524')
  await dialog.getByPlaceholder('搜索成员邮箱或名称').fill('child010');await page.waitForFunction(()=>document.querySelectorAll('[role=dialog] tbody tr').length===1)
  await dialog.getByRole('button',{name:'母号移出',exact:true}).click();assert.ok(!writes.some(r=>r.p.endsWith('/members/remove')))
  await dialog.getByRole('button',{name:'确认操作',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('.confirm-box'))
  assert.equal(writes.find(r=>r.p.endsWith('/members/remove')).body.method,'mother_kick')
  await page.screenshot({path:path.join(out,'members-desktop.png'),fullPage:true})
  await dialog.getByRole('button',{name:'关闭',exact:true}).click()
  assert.equal(await page.getByRole('tab',{name:'席位恢复',exact:true}).count(),0)
  await section.getByRole('switch',{name:'母号 A 接单状态',exact:true}).click()
  assert.equal(writes.find(r=>r.p==='/api/admin/refund/mother-management/mother/resource').body.enabled,false)
  await section.getByRole('button',{name:'查看成员',exact:true}).click()
  await page.getByRole('dialog').getByPlaceholder('搜索成员邮箱或名称').waitFor()
  await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).click()
  await section.getByRole('button',{name:'刷新全部席位',exact:true}).click()
  await section.getByRole('button',{name:'刷新全部套餐',exact:true}).click()
  assert.equal(calls.filter(([m,p])=>p.endsWith('/mother/capacity')).length,1)
  assert.equal(calls.filter(([m,p])=>p.endsWith('/mother/plan')).length,1)
  for(const width of [1440,390]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await page.screenshot({path:path.join(out,`resources-${width}.png`),fullPage:true})}
  assert.deepEqual(errors,[])
  assert.ok(calls.some(([m,p,q])=>p.endsWith('/members')&&q.page_size==='500'&&q.page==='2'))
  console.log(JSON.stringify({browserErrors:errors.length,memberRows:525,paging:[10,500],mobileWidth:390,credentialStorage:'none',inlineWrites:writes.filter(w=>w.p==='/api/admin/refund/mother-management/mother/resource').length}))
 }finally{await browser.close()}
})
