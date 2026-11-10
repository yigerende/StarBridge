// Run against the isolated preview: node --test frontend/tests/registrationNavigation.spec.mjs
import {test} from 'node:test'
import assert from 'node:assert/strict'
import {chromium} from '@playwright/test'

test('claim links keep the account page and order filter through navigation', async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_BROWSER_CHANNEL||undefined})
 const context=await browser.newContext()
 const base='http://127.0.0.1:18970'
 const stamp=new Date().toISOString(),expiry=new Date(Date.now()+7200000).toISOString()
 const orders=['fixture-one','fixture-two'].map(id=>({id,status:'completed',quantity:1,delivered:1,unit_price:5,charged:5,released:0,email_provider:'gmail',created_at:stamp,updated_at:stamp}))
 const reads=[],errors=[]
 try{
  const login=await context.request.post(base+'/api/auth/login',{data:{email:'admin',password:'admin'},headers:{Origin:base}})
  assert.equal(login.status(),200)
  await context.route('**/registration/api/user/v1/registration-orders?**',r=>r.fulfill({json:{items:orders,total:2,limit:20,offset:0}}))
  await context.route('**/registration/api/user/v1/account-orders?**',r=>{
   const id=new URL(r.request().url()).searchParams.get('order_id')||'';reads.push(id)
   return r.fulfill({json:{items:orders.filter(o=>!id||o.id===id).map(o=>({...o,order_id:o.id,available_count:1,download_expires_at:expiry,last_delivered_at:stamp})),total:id?1:2,accounts_total:id?1:2,server_time:stamp}})
  })
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message))
  await page.goto(base+'/?business=registration#/orders')
  const claim=page.getByRole('link',{name:'领取账号 ↗'}).first()
  await claim.waitFor()
  const menu=await page.locator('.customer-nav-group>.customer-nav-row').allTextContents()
  assert.ok(menu.findIndex(x=>x.includes('注册办理'))<menu.findIndex(x=>x.includes('订阅办理')))
  assert.equal(menu.findIndex(x=>x.includes('代退办理')),menu.findIndex(x=>x.includes('订阅办理'))+1)
  assert.equal(await page.getByRole('link',{name:'代退办理',exact:true}).getAttribute('href'),'#/refund')
  const href=await claim.getAttribute('href')
  assert.equal(href,'/?business=registration#/accounts?order_id=fixture-one')
  await claim.click()
  await page.getByRole('heading',{name:'我的账号',exact:true}).waitFor()
  await page.getByRole('button',{name:'展开订单 fixture-one',exact:true}).waitFor()
  assert.equal(await page.getByRole('button',{name:'展开订单 fixture-two',exact:true}).count(),0)
  assert.equal(reads.at(-1),'fixture-one')
  assert.equal(await page.getByText('此功能暂未开放',{exact:true}).count(),0)
  await page.reload()
  await page.getByRole('button',{name:'展开订单 fixture-one',exact:true}).waitFor()
  // Same-page query changes and back/forward must replace, not retain, the prior filter.
  await page.evaluate(()=>{location.hash='/accounts?order_id=fixture-two'})
  await page.getByRole('button',{name:'展开订单 fixture-two',exact:true}).waitFor()
  assert.equal(await page.getByRole('button',{name:'展开订单 fixture-one',exact:true}).count(),0)
  await page.goBack();await page.getByRole('button',{name:'展开订单 fixture-one',exact:true}).waitFor()
  await page.goForward();await page.getByRole('button',{name:'展开订单 fixture-two',exact:true}).waitFor()
  // Following the actual href in a fresh tab must also work without an SPA click handler.
  const tab=await context.newPage();await tab.goto(base+href)
  await tab.getByRole('button',{name:'展开订单 fixture-one',exact:true}).waitFor()
  await tab.setViewportSize({width:390,height:844})
  assert.equal(await tab.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false)
  await tab.close()
  await page.getByRole('link',{name:'我的账号',exact:true}).click()
  await page.getByRole('button',{name:'展开订单 fixture-one',exact:true}).waitFor()
  await page.getByRole('button',{name:'展开订单 fixture-two',exact:true}).waitFor()
  assert.equal(reads.at(-1),'')
  assert.deepEqual(errors,[])
 }finally{
  await context.request.post(base+'/api/auth/logout',{headers:{Origin:base}})
  await browser.close()
 }
})
