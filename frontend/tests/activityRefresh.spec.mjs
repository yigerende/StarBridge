import {test} from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import {chromium} from '@playwright/test'

test('activity auto refresh and immediate filters work for customer and operator',async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true})
 try{for(const role of ['admin','member']){
  const ctx=await browser.newContext({viewport:{width:1440,height:1000}}),calls=[],errors=[]
  const root=path.resolve('../webui')
  await ctx.route('**/*',async route=>{
   const url=new URL(route.request().url()),p=url.pathname
   if(p==='/'||p.startsWith('/assets/'))return route.fulfill({path:path.join(root,p==='/'?'index.html':p.slice(1))})
   let json={items:[]}
   if(p==='/api/public')json={site:{name:'星桥'},products:[],countries:[],features:{activity:true},announcements:[]}
   if(p==='/api/me')json={id:'refresh-'+role,role,name:'测试',balance:0,frozen:0,navigation:[{id:'activity',path:'/activity',label:'办理总览',group:'main'}]}
   if(p.endsWith('/activity')){
    calls.push(Object.fromEntries(url.searchParams));const total=30+calls.length
    json={items:[{id:'fixture',business:'refund',workflow:'refund',title:'自动更新 '+calls.length,email:'fixture@example.test',owner:'fixture@example.test',state:'active',status:'processing',settled:0,frozen:100,created_at:new Date().toISOString(),updated_at:new Date().toISOString()}],total,summary:{total,active:total,customers:1},businesses:[],show_fees:true}
   }
   return route.fulfill({json})
  })
  const page=await ctx.newPage();page.on('pageerror',e=>errors.push(e.message))
  await page.clock.install()
  await page.goto('http://127.0.0.1:18970/#/'+(role==='admin'?'admin/':'')+'activity')
  await page.getByText('自动更新 1',{exact:true}).waitFor()
  let count=calls.length
  await page.clock.fastForward(11000)
  await page.getByText('自动更新 '+(count+1),{exact:true}).waitFor()
  assert.equal(calls.length,count+1)
  assert.equal(await page.getByRole('button',{name:'筛选',exact:true}).count(),0)
  await page.locator('.activity-filters select').nth(0).selectOption('refund')
  await page.getByText('自动更新 '+(count+2),{exact:true}).waitFor()
  assert.equal(calls.at(-1).business,'refund')
  await page.locator('.activity-filters select').nth(1).selectOption('failed')
  await page.waitForFunction(()=>document.querySelector('.activity-list-heading')?.textContent.includes('34'))
  assert.equal(calls.at(-1).state,'failed')
  count=calls.length
  await page.getByPlaceholder(role==='admin'?'订单号、客户邮箱、办理账号或项目':'订单号、办理账号或项目').fill('search-fixture')
  assert.equal(calls.length,count)
  await page.clock.fastForward(350)
  await page.getByText('自动更新 '+(count+1),{exact:true}).waitFor()
  assert.equal(calls.at(-1).q,'search-fixture')
  if(role==='admin'){
   await page.getByLabel('包含管理员',{exact:true}).uncheck()
   await page.getByText('自动更新 '+(calls.length),{exact:true}).waitFor()
   assert.equal(calls.at(-1).include_admin,'false')
  }
  await page.getByRole('button',{name:'本月',exact:true}).click()
  await page.waitForFunction(()=>document.querySelector('button.active[aria-pressed]')?.textContent==='本月')
  await page.waitForTimeout(0)
  assert.ok(calls.at(-1).start.endsWith('-01'))
  await page.getByRole('button',{name:'重置',exact:true}).click()
  await page.clock.fastForward(350)
  await page.waitForTimeout(0)
  assert.equal(calls.at(-1).q,'');assert.equal(calls.at(-1).business,'');assert.equal(calls.at(-1).page,'1')
  count=calls.length
  await page.getByLabel('自动刷新',{exact:true}).uncheck();await page.clock.fastForward(21000)
  assert.equal(calls.length,count)
  await page.getByLabel('自动刷新',{exact:true}).check();await page.clock.fastForward(11000)
  await page.getByText('自动更新 '+(count+1),{exact:true}).waitFor()
  await page.setViewportSize({width:390,height:844})
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false)
  assert.deepEqual(errors,[])
  console.log(JSON.stringify({role,requests:calls.length,autoRefresh:true,immediateFilters:true,mobileOverflow:false}))
  await ctx.close()
 }}finally{await browser.close()}
})
