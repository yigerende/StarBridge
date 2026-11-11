import {test} from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import fs from 'node:fs'
import {chromium} from '@playwright/test'

const output=path.resolve('../.local/proxy-policies');fs.mkdirSync(output,{recursive:true})
async function browserFixture(handler){
 const browser=await chromium.launch({channel:'chrome',headless:true})
 const context=await browser.newContext({viewport:{width:1440,height:1000}});context.setDefaultTimeout(10000)
 const errors=[],root=path.resolve('../webui')
 await context.route('**/*',async route=>{
  const req=route.request(),p=new URL(req.url()).pathname
  if(p==='/'||p.startsWith('/assets/'))return route.fulfill({path:path.join(root,p==='/'?'index.html':p.slice(1))})
  let result=await handler(req,p)
  if(result===undefined)result=p==='/api/public'?{site:{name:'星桥',english_name:'StarBridge'},features:{},announcements:[],products:[]}:p==='/api/me'?{id:'admin',name:'管理员',email:'admin@fixture.invalid',role:'admin',features:{},navigation:[],custom_menus:[]}:{items:[]}
  return route.fulfill({json:result})
 })
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message))
 return {browser,page,errors}
}
test('refund proxy targets, ordered backups and mobile layout',async()=>{
 const writes=[]
 const data={runtime_ready:true,settings:{enabled:false,max_parallel:2,max_attempts:2,retry_seconds:30,mother_interval_seconds:0,child_proxy_id:'primary',child_proxy_backups:[],child_proxy_cooldown_seconds:300,mother_failover:{enabled:true,max_switches:3,retry_seconds:30},recovery:{enabled:true,max_retries:3,verify_intervals:[30],cleanup_intervals:[15],alert_seconds:600,cleanup_failed:true}},proxies:[{id:'primary',name:'主用厂商',target:'both',enabled:true},{id:'backup1',name:'备用厂商 A',target:'child',enabled:true},{id:'backup2',name:'备用厂商 B',target:'child',enabled:true},{id:'mother',name:'母号专用',target:'mother',enabled:true}],mothers:[]}
 const {browser,page,errors}=await browserFixture((req,p)=>{
  if(p==='/api/admin/refund/settings'){
   if(req.method()==='PUT'){data.settings=req.postDataJSON().settings;writes.push(data.settings);return {ok:true}}
   return data
  }
  if(p==='/api/admin/refund/proxies'){const row=req.postDataJSON();data.proxies.push({...row,id:'created'});writes.push(row);return {ok:true}}
 })
 try{
  await page.goto('http://127.0.0.1:18970/#/admin/refund')
  await page.getByRole('tab',{name:'调度与恢复'}).click()
  assert.equal(await page.getByLabel('主用代理',{exact:true}).locator('option').filter({hasText:'母号专用'}).count(),0)
  await page.getByRole('checkbox',{name:'备用厂商 A'}).check();await page.getByRole('checkbox',{name:'备用厂商 B'}).check()
  await page.locator('.child-proxy-priority li').filter({hasText:'备用厂商 B'}).getByRole('button',{name:'上移'}).click()
  await page.getByLabel('故障冷却（秒）',{exact:false}).fill('180')
  await page.getByRole('button',{name:'应用调度配置'}).click()
  await page.waitForFunction(()=>document.querySelector('button.btn.primary')?.disabled===false)
  assert.deepEqual(writes[0].child_proxy_backups,['backup2','backup1']);assert.equal(writes[0].child_proxy_cooldown_seconds,180)
  for(const width of [1440,1024,390]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await page.screenshot({path:path.join(output,`refund-${width}.png`),fullPage:true})}
  await page.setViewportSize({width:1440,height:1000});await page.getByRole('tab',{name:'代退代理',exact:true}).click()
  await page.getByRole('button',{name:'录入代理'}).click();await page.getByLabel('代理名称',{exact:true}).fill('第三厂商')
  await page.getByRole('dialog').getByRole('combobox').selectOption('child');await page.getByLabel('代理地址',{exact:true}).fill('http://fixture.invalid:8080')
  await page.getByRole('dialog').getByRole('button',{name:'保存',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'})
  assert.equal(writes[1].target,'child');assert.deepEqual(errors,[])
 }finally{await browser.close()}
})
test('subscription three modes, independent country inventory and import',async()=>{
 let mode='country_pool',imported=false;const writes=[]
 const base='/registration/api/admin/v1/subscription-proxies'
 const snapshot=()=>({settings:{renewal_country:'PH'},dynamic:{total:imported?1:0,providerSettings:{},countryPools:imported?[{countryCode:'PH',total:1,manualCount:1,sources:[],entries:[]}]:[]}})
 const {browser,page,errors}=await browserFixture((req,p)=>{
  if(p==='/api/admin/settings')return {proxy:{mode}}
  if(p==='/api/admin/resources')return {items:[]}
  if(p==='/api/admin/country-pool')return {settings:{auth_type:'credentials',renewal_country:'PH',lifetime_seconds:3600,max_parallel:20},countries:[]}
  if(p==='/api/admin/proxy-mode'){mode=req.postDataJSON().mode;writes.push(mode);return {ok:true}}
  if(p===base)return snapshot()
  if(p===base+'/inventory')return {items:[],total:0,countries:[],providers:[],summary:{total:0},test:null}
  if(p===base+'/preview')return {valid:1,invalid:0,duplicate:0,items:[{line:1,kind:'proxy',url:'http://fixture.invalid:8080',countryCode:'PH',protocol:'http'}]}
  if(p===base+'/import'){imported=true;writes.push(req.postDataJSON());return {operation:'job'}}
  if(p===base+'/operations/job')return {state:'completed',result:snapshot()}
  if(p.startsWith(base+'/pools/'))return {items:[{id:'fake',url:'http://fixture.invalid:8080'}],total:1,offset:0,limit:50}
 })
 try{
  await page.goto('http://127.0.0.1:18970/#/admin/resources')
  await page.getByRole('button',{name:'独立会话代理',exact:true}).click();await page.getByRole('heading',{name:'连接与会话'}).waitFor()
  await page.getByRole('button',{name:'国家代理',exact:true}).click();await page.getByRole('heading',{name:'国家代理',exact:true}).waitFor()
  await page.locator('.national-proxies textarea').fill('http://fixture.invalid:8080')
  await page.getByRole('button',{name:'识别并预览'}).click();await page.getByLabel('第 1 行国家').fill('PH')
  await page.getByRole('button',{name:'合并到国家池'}).click();await page.locator('.country-pool-heading').waitFor()
  await page.getByRole('button',{name:'启用国家代理',exact:true}).click();await page.getByText('当前模式：国家代理',{exact:true}).waitFor()
  assert.equal(writes[0].overrides[0].countryCode,'PH');assert.equal(writes[1],'national_pool')
  for(const width of [1440,1024,390]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await page.screenshot({path:path.join(output,`subscription-${width}.png`),fullPage:true})}
  assert.deepEqual(errors,[])
 }finally{await browser.close()}
})
