import {test} from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import fs from 'node:fs'
import {chromium} from '@playwright/test'

test('batch skips disabled mothers, reports each outcome and continues past HTML errors',async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true})
 const context=await browser.newContext({viewport:{width:1440,height:1000}})
 context.setDefaultTimeout(10000)
 const root=path.resolve('../webui'),out=path.resolve('../.local/mother-batch');fs.mkdirSync(out,{recursive:true})
 const capacity={standard:{total:2,remaining:1,held:0},premium:{total:0,remaining:0,held:0},checked_at:'2026-11-20T01:00:00Z'}
 const mothers=['2','3','4','5'].map((name,i)=>({id:name,name,email:`mother${name}@example.test`,team_id:'team-'+name,enabled:i>0,has_secret:true,has_refresh:false,proxy_id:'proxy',seat_type:'default',completed:10,management_json:{capacity},token_expires_at:'2026-12-01T00:00:00Z'}))
 const settings={enabled:false,max_parallel:2,max_attempts:2,retry_seconds:30,mother_interval_seconds:0,child_proxy_id:'',child_proxy_backups:[],child_proxy_cooldown_seconds:300,mother_failover:{enabled:true,max_switches:3,retry_seconds:30},resource_recovery:{},recovery:{enabled:true,max_retries:3,verify_intervals:[30],cleanup_intervals:[15],alert_seconds:600,cleanup_failed:true}}
 const calls=[],errors=[];let mode='partial'
 await context.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname
  if(p==='/'||p.startsWith('/assets/'))return route.fulfill({path:path.join(root,p==='/'?'index.html':p.slice(1))})
  let json={items:[],total:0,index:[]}
  if(p==='/api/public')json={site:{name:'星桥'},features:{},products:[],countries:[],announcements:[]}
  if(p==='/api/me')json={id:'admin',name:'管理员',role:'admin',features:{},navigation:[]}
  if(p==='/api/admin/refund/settings')json={settings,runtime_ready:true,mothers,proxies:[],resource_summary:{mothers:{total:4,enabled:3},proxies:{total:0,enabled:0}}}
  if(p==='/api/admin/refund/resources/mothers')json={items:mothers,total:4,page:1,page_size:10}
  if(p==='/api/admin/refund/mother-management/summary')json={items:mothers}
  if(p==='/api/admin/refund/mother-management/settings')json={auto_refresh:true}
  const match=p.match(/mother-management\/(\d+)\/(capacity|plan)$/)
  if(match){
   const [,id,kind]=match;calls.push({id,kind,activeOnly:url.searchParams.get('active_only')})
   if(mode==='partial'&&id==='3')return route.fulfill({status:502,contentType:'text/html',body:'<!DOCTYPE html><html>private diagnostic</html>'})
   if(mode==='partial'&&id==='4')return route.fulfill({status:400,json:{error:'母号凭据已过期，请更新 Session / AT 后重试'}})
   if(mode==='race'&&id==='4'){mothers.find(m=>m.id===id).enabled=false;json={skipped:true,reason:'disabled'}}else json={...capacity}
  }
  return route.fulfill({json})
 })
 try{
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message))
  await page.goto('http://127.0.0.1:18970/#/admin/refund')
  const section=page.locator('#refund-resource-mothers')
  await section.getByText('已启用母号席位快照 · 已查询 3 / 3 · 已停用 1 个',{exact:true}).waitFor()
  assert.equal(await section.locator('.mother-metrics article').first().locator('strong').innerText(),'3 / 6')
  await section.getByRole('button',{name:'刷新全部席位',exact:true}).click()
  await section.getByRole('status').getByText('席位刷新完成：成功 1，失败 2，跳过 1 个已停用母号。',{exact:true}).waitFor()
  await section.getByRole('alert').getByText(/3：请求未完成，请稍后重试（HTTP 502）.*4：母号凭据已过期/).waitFor()
  assert.deepEqual(calls.map(c=>c.id),['3','4','5'])
  assert.ok(calls.every(c=>c.activeOnly==='true'))
  assert.doesNotMatch(await section.innerText(),/Unexpected|DOCTYPE|private diagnostic/)
  for(const width of [1440,390]){
   await page.setViewportSize({width,height:1000})
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false)
   await page.screenshot({path:path.join(out,`result-${width}.png`),fullPage:true})
  }
  mode='race'
  await section.getByRole('button',{name:'刷新全部套餐',exact:true}).click()
  await section.getByRole('status').getByText('套餐刷新完成：成功 2，失败 0，跳过 2 个已停用母号。',{exact:true}).waitFor()
  assert.equal(await section.getByRole('alert').count(),0)
  mode='ok'
  await section.getByRole('button',{name:'刷新全部席位',exact:true}).click()
  await section.getByRole('status').getByText('席位刷新完成：成功 2，失败 0，跳过 2 个已停用母号。',{exact:true}).waitFor()
  assert.deepEqual(calls.slice(-2).map(c=>c.id),['3','5'])
  assert.deepEqual(errors,[])
 }catch(error){console.error('Browser errors:',errors);throw error}finally{await browser.close()}
})
