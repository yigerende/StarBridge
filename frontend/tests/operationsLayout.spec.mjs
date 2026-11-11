import {test} from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import {mkdirSync} from 'node:fs'
import {chromium} from '@playwright/test'

test('operator pending menu and chart order, customer workspace has no version controls',async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true}),context=await browser.newContext({viewport:{width:1440,height:1000}})
 const root=path.resolve('../webui'),output=path.resolve('../.local/operations-layout');mkdirSync(output,{recursive:true})
 const errors=[],requests=[];let role='admin'
 const queue={queued:0,running:0,review:0,accepting:true,paused:false,period_completed:0,period_settled:0,delivered:0,settling:0}
 await context.route('**/*',route=>{
  const p=new URL(route.request().url()).pathname;requests.push(p)
  if(p==='/'||p.startsWith('/assets/'))return route.fulfill({path:path.join(root,p==='/'?'index.html':p.slice(1))})
  let json={items:[]}
  if(p==='/api/public')json={site:{name:'星桥'},features:{},announcements:[],products:[]}
  if(p==='/api/me')json={id:'fixture',role,name:'测试',features:{},navigation:[]}
  if(p==='/api/admin/system/update')json={current_version:'private-build',latest_version:'next-build',available:true,phase:'completed'}
  if(p==='/api/admin/overview')json={totals:{orders:0,completed:0,partial:0,settled:0,attention:7},queues:{registration:queue,subscription:queue,refund:queue},attention:[{key:'risk',title:'风险事件待核查',count:7,unit:'项',hint:'全站未核查事件',href:'#/admin/risk'}],recent_orders:[],trend:[],start:'2026-11-11',end:'2026-11-11',generated_at:new Date().toISOString()}
  if(p==='/api/wallet')json={balance:0,frozen:0,ledger:[],recharges:[]}
  return route.fulfill({json})
 })
 try{
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message))
  await page.goto('http://127.0.0.1:18970/#/admin');await page.locator('.dash-attention-menu summary').waitFor()
  assert.ok(await page.locator('.version-badge').count());assert.match(await page.locator('.dash-attention-menu summary').innerText(),/7/)
  assert.equal(await page.evaluate(()=>{const h=[...document.querySelectorAll('.admin-dashboard h2')].map(e=>e.textContent);return h.indexOf('最近订单')<h.indexOf('业务趋势')}),true)
  for(const width of [1440,390]){await page.setViewportSize({width,height:1000});await page.locator('.dash-attention-menu').evaluate(e=>e.open=true);await page.getByRole('link',{name:/风险事件待核查/}).waitFor();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await page.screenshot({path:path.join(output,`pending-${width}.png`)});await page.locator('.dash-attention-menu summary').press('Escape');assert.equal(await page.locator('.dash-attention-menu').getAttribute('open'),null)}
  for(const userRole of ['admin','member']){role=userRole;requests.length=0;await page.goto('http://127.0.0.1:18970/#/wallet');await page.reload();await page.getByRole('heading',{name:'资金中心',exact:true}).waitFor();assert.equal(await page.locator('.system-update,.version-badge').count(),0);assert.equal(requests.includes('/api/admin/system/update'),false);assert.doesNotMatch(await page.locator('body').innerText(),/private-build|next-build|版本与更新/)}
  assert.deepEqual(errors,[])
 }finally{await browser.close()}
})
