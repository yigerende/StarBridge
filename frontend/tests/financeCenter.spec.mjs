import {test} from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import {mkdirSync} from 'node:fs'
import {chromium} from '@playwright/test'

test('operator finance tabs, customer search, pagination and ledger stay read-only',async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true})
 const context=await browser.newContext({viewport:{width:1440,height:1000}});context.setDefaultTimeout(10000)
 const root=path.resolve('../webui'),output=path.resolve('../.local/finance-center');mkdirSync(output,{recursive:true})
 const requests=[],writes=[],errors=[]
 const customer={id:'customer',name:'测试客户',email:'customer@example.test',balance:20000,frozen:500,credited:30000,adjusted:-100,rewarded:100,registration:2000,subscription:6000,refund:2000,charged:11000,returned:1000}
 await context.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname;requests.push(url)
  if(p==='/'||p.startsWith('/assets/'))return route.fulfill({path:path.join(root,p==='/'?'index.html':p.slice(1))})
  if(req.method()!=='GET')writes.push(p)
  let json={items:[],counts:{},maxParallel:10,available:10}
  if(p==='/api/public')json={site:{name:'星桥'},features:{},announcements:[],products:[],registration_enabled:true}
  if(p==='/api/me'||p==='/registration/api/user/v1/me')json={id:'admin',email:'admin@example.test',name:'管理员',role:'admin',features:{},navigation:[]}
  if(p==='/registration/api/public')json={features:{}}
  if(p==='/api/admin/finance/overview')json={totals:{credited:30000,spent:10000,adjusted:-100},current:{available:19500,frozen:500},trend:[]}
  if(p==='/api/admin/finance')json={totals:{spent:6000},users:[{...customer,spent:6000,orders:3}]}
  if(p==='/api/admin/finance/customers')json={items:[customer],total:60,page:Number(url.searchParams.get('page')||1),has_more:true}
  if(p==='/api/admin/finance/customers/customer')json={user:customer,totals:customer,total:30,page:Number(url.searchParams.get('page')||1),has_more:url.searchParams.get('page')!=='2',start:'2026-11-01',end:'2026-11-11',items:[{id:1,kind:'charge',business:'subscription',available_delta:0,frozen_delta:-100,balance_after:20000,frozen_after:500,description:'订阅服务费',created_at:'2026-11-11T00:00:00Z'}]}
  return route.fulfill({json})
 })
 try{
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message))
  await page.goto('http://127.0.0.1:18970/#/admin/finance');await page.getByRole('heading',{name:'资金变动趋势'}).waitFor()
  assert.ok(!requests.some(u=>u.pathname==='/api/admin/finance'||u.pathname==='/api/admin/finance/customers'))
  const tabs=page.getByRole('tablist',{name:'资金中心页面'});assert.equal(await tabs.getByRole('tab').count(),3)
  await tabs.getByRole('tab',{name:'业务明细',exact:true}).click()
  await page.getByRole('tab',{name:'订阅',exact:true}).click();await page.getByRole('heading',{name:'订阅服务消费'}).waitFor()
  await page.getByRole('tab',{name:'代退',exact:true}).click();await page.getByRole('heading',{name:'代退服务消费'}).waitFor()
  await tabs.getByRole('tab',{name:'客户资金明细',exact:true}).click();await page.getByRole('button',{name:'查看流水',exact:true}).waitFor()
  await page.getByLabel('搜索资金账户').fill('customer@example.test');await Promise.all([page.waitForResponse(r=>r.url().includes('/finance/customers?')&&new URL(r.url()).searchParams.get('q')==='customer@example.test'),page.getByRole('button',{name:'查询客户'}).click()])
  await page.getByRole('button',{name:'下一页客户'}).click();await page.waitForFunction(()=>document.querySelector('.customer-finance .pagination')?.textContent.includes('2 / 6'))
  await page.getByLabel('每页资金账户数').selectOption('50');await page.waitForFunction(()=>document.querySelector('.customer-finance .pagination')?.textContent.includes('1 / 2'))
  for(const width of [1440,390]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await page.screenshot({path:path.join(output,`customers-${width}.png`),fullPage:true})}
  await page.getByRole('button',{name:'查看流水'}).click();const dialog=page.getByRole('dialog',{name:'客户资金流水'});await dialog.getByText('订阅服务费',{exact:true}).waitFor()
  await dialog.getByRole('button',{name:'下一页流水'}).click();await page.waitForFunction(()=>document.querySelector('[role=dialog] .pagination')?.textContent.includes('2 / 2'))
  for(const width of [1440,390]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await page.screenshot({path:path.join(output,`ledger-${width}.png`),fullPage:true})}
  await dialog.getByRole('button',{name:/^关闭(窗口)?$/}).click()
  assert.deepEqual(errors,[]);assert.deepEqual(writes,[])
 }finally{await browser.close()}
})
