// All API requests are intercepted; this test never creates an order or pays.
import {test} from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import {chromium} from '@playwright/test'

test('subscription and checkout buttons use their own availability switches',async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_BROWSER_CHANNEL||'chrome'})
 const base=process.env.STARBRIDGE_UI_TEST_URL||'http://127.0.0.1:18970'
 try{
  for(const scenario of [
   {name:'subscription with links closed',route:'/subscribe',subscribe:true,links:false,connected:true},
   {name:'links with subscription closed',route:'/links',subscribe:false,links:true,connected:true},
   {name:'both services open',route:'/subscribe',subscribe:true,links:true,connected:true},
   {name:'subscription service unavailable',route:'/subscribe',subscribe:true,links:false,connected:false},
   {name:'link service unavailable',route:'/links',subscribe:false,links:true,connected:false},
  ]){
   const context=await browser.newContext({viewport:{width:1440,height:1000}})
   context.setDefaultTimeout(10000)
   const submitted=[],errors=[]
   const features={subscribe:scenario.subscribe,links:scenario.links,orders:false,wallet:true}
   const pub={site:{name:'星桥',english_name:'StarBridge'},features,products:[{id:'pro',name:'Pro',fee:10000}],countries:[{code:'US',name:'美国',currency:'USD'}],default_country:'US',announcements:[],subscription_flow:'cslive_go_direct_pay',payment_connected:scenario.connected&&scenario.subscribe,checkout_connected:scenario.connected&&scenario.links}
   await context.route('**/*',route=>{
    const url=new URL(route.request().url())
    if(url.origin!==new URL(base).origin)return route.abort()
    if(!process.env.STARBRIDGE_UI_ASSETS)return route.continue()
    if(url.pathname==='/'||url.pathname.startsWith('/assets/')){
     const root=path.resolve(process.env.STARBRIDGE_UI_ASSETS)
     const file=path.resolve(root,url.pathname==='/'?'index.html':'.'+url.pathname)
     assert.ok(file.startsWith(root+path.sep))
     return route.fulfill({path:file})
    }
    return route.fulfill({status:404,body:''})
   })
   await context.route('**/api/**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname
    if(request.method()==='POST'&&path.endsWith('/orders')){
     submitted.push(request.postDataJSON())
     return route.fulfill({status:503,json:{error:'测试请求已拦截'}})
    }
    let json={items:[]}
    if(path==='/api/public')json=pub
    if(path==='/api/me')json={id:'fixture-customer',name:'测试客户',email:'customer@example.test',role:'user',balance:100000,frozen:0,features,navigation:[],custom_menus:[]}
    if(path==='/api/quote')json={fee:10000}
    if(path==='/api/session/inspect')json={email:'subscription@example.test',plan:'free',expires_at:new Date(Date.now()+86400000).toISOString()}
    if(path==='/api/subscription-prices')json={rows:[],fx_updated_at:null,fx_stale:false,scheduled:true,price_interval_hours:1}
    return route.fulfill({json})
   })
   const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message))
   await page.goto(base+'/#'+scenario.route)
   const full=scenario.route==='/subscribe'
   const button=page.getByRole('button',{name:full?'开始订阅办理':'生成结账链接',exact:true})
   await page.getByPlaceholder('粘贴完整 Session JSON，自动识别订阅账号，无需手填邮箱。',{exact:true}).fill(JSON.stringify({sessionToken:'fixture-not-a-real-token'}))
   await page.getByText('subscription@example.test',{exact:true}).first().waitFor()
   assert.equal(await button.isEnabled(),false,'consent/card are still required')
   if(full){
    await page.locator('#subscription-card-line').fill(`4242424242424242|12|${new Date().getFullYear()+3}|123`)
    await page.locator('.card-line-ready').waitFor()
   }
   await page.locator('.confirmation-consent input').check()
   assert.equal(await page.getByRole('button',{name:'保存订阅方案',exact:true}).isEnabled(),true)
   assert.equal(await button.isEnabled(),scenario.connected,scenario.name)
   if(scenario.connected){
    await button.click()
    await page.getByText('测试请求已拦截',{exact:true}).waitFor()
    assert.equal(submitted.length,1)
    assert.equal(submitted[0].workflow,full?'subscription':undefined)
    assert.equal(Boolean(submitted[0].card),full)
    assert.equal(submitted[0].prepare,true)
   }else{
    await page.getByText(full?'订阅办理暂未开放。':'结账链接暂未开放。',{exact:true}).waitFor()
    await page.locator('form.checkout-grid').evaluate(form=>form.requestSubmit())
    await page.waitForTimeout(150)
    assert.equal(submitted.length,0,'form submission must obey the same service switch')
   }
   await page.setViewportSize({width:390,height:844})
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false)
   assert.deepEqual(errors,[])
   await context.close()
  }
 }finally{await browser.close()}
})
