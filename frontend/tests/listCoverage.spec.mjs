// All requests are intercepted. Browser interactions never reach business services.
import {test} from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import {chromium,expect} from '@playwright/test'

const root=path.resolve('../webui')
const tier={code:'base',name:'普通会员',threshold:0,discount:100}
const rows=Array.from({length:525},(_,i)=>({
 id:`row${String(i).padStart(3,'0')}`,order_id:`order${String(i).padStart(3,'0')}`,
 email:`member${String(i).padStart(3,'0')}@example.test`,name:`Customer ${i}`,title:`Record ${i}`,owner:'owner@example.test',user_email:'owner@example.test',owner_email:'owner@example.test',
 product_name:'Plus',fee:i,quantity:i+1,delivered:1,available_count:1,need_oauth:i%2===1,
 status:'succeeded',state:'completed',activation:'active',renewal:'disabled',fee_status:'settled',workflow:'subscription',business:'subscription',
 created_at:'2026-11-12T00:00:00Z',updated_at:'2026-11-12T01:00:00Z',last_delivered_at:'2026-11-12T01:00:00Z',download_expires_at:'2099-11-12T00:00:00Z',
 payment:{state:'succeeded',fee_state:'charged'},timing:{total_seconds:60},price_snapshot:{},
 amount:i,available_delta:i,frozen_delta:0,balance_after:1000,frozen_after:0,kind:'charge',description:'Synthetic row '+i,
 source:'web',balance:1000,frozen:0,used_balance:i,membership:{current:tier},role:'member',prefix:'KEY-'+i,copyable:true,revoked:false,
 reward_state:'rewarded',entitled:i,inviter:'inviter@example.test',reference:'reference'+i,action:'login',actor:'Operator',target:'fixture',ip:'192.0.2.1',
 unit_price:1,charged:1,released:0,completed:1,account_count:1,
 enabled:true,vendor:'other',country:'US',capacity:10,in_use:0,team_id:'team-fixture',has_secret:false,proxy_id:'',seat_type:'default',target:'both',recipient:'fixture@example.test',purpose:'test',
}))

async function harness(browser,admin){
 const context=await browser.newContext({viewport:{width:1440,height:1000}});context.setDefaultTimeout(10000)
 const calls=[],errors=[]
 await context.route('**/*',route=>{
  const request=route.request(),url=new URL(request.url()),p=url.pathname,params=Object.fromEntries(url.searchParams)
  if(p==='/'||p.startsWith('/assets/'))return route.fulfill({path:path.join(root,p==='/'?'index.html':p.slice(1))})
  calls.push({p,params})
  const limit=Number(params.page_size||params.limit||10),offset=params.offset?Number(params.offset):(Number(params.page||1)-1)*limit
  let selected=rows.filter(r=>(!params.q||JSON.stringify(r).includes(params.q))&&(!params.mode||(r.need_oauth?'oauth':'standard')===params.mode)&&(!params.order_id||r.order_id===params.order_id))
  selected=[...selected].sort((a,b)=>(params.sort_dir==='asc'?1:-1)*String(a[params.sort_by]??a.id).localeCompare(String(b[params.sort_by]??b.id),undefined,{numeric:true}))
  let json={items:selected.slice(offset,offset+limit),total:selected.length,page:Number(params.page||1),page_size:limit,offset,limit,summary:{},stats:{},rules:{enabled:true,stages:[]},products:[],tiers:[tier],businesses:[],show_fees:true,counts:{},maxParallel:10,available:10,accounts_total:525,server_time:new Date().toISOString()}
  if(p==='/api/public'||p==='/registration/api/public')json={site:{name:'星桥'},brand:{name:'星桥'},features:{},products:[],countries:[],announcements:[],registration_enabled:true}
  if(p==='/api/me'||p==='/registration/api/user/v1/me')json={id:admin?'operator':'customer',email:'fixture@example.test',name:'Test',role:admin?'admin':'member',balance:1000,frozen:0,features:{},navigation:[],membership:{current:tier},custom_menus:[]}
  if(p==='/api/announcements/query')json={items:[],total:0,index:[]}
  if(p==='/api/admin/settings')json={proxy:{mode:'manual'},mail:{ready:false},site:{name:'星桥'}}
  if(p==='/api/admin/refund/settings')json={runtime_ready:true,resource_summary:{mothers:{total:525,enabled:525},proxies:{total:525,enabled:525}},settings:{enabled:false,max_parallel:2,max_attempts:2,retry_seconds:30,mother_interval_seconds:0,child_proxy_id:'',child_proxy_backups:[],child_proxy_cooldown_seconds:300,mother_failover:{enabled:true,max_switches:3,retry_seconds:30},recovery:{enabled:true,max_retries:3,verify_intervals:[30],cleanup_intervals:[15],alert_seconds:600,cleanup_failed:true}},proxies:[],mothers:[]}
  if(p==='/api/admin/refund/mother-management/summary')json={items:[]}
  if(p==='/api/admin/refund/mother-management/settings')json={auto_refresh:false}
  if(p==='/api/wallet')json={balance:1000,frozen:0,items:[],ledger:[],topups:[],rules:{}}
  if(p==='/api/order-updates')return route.fulfill({status:200,contentType:'text/event-stream',body:''})
  return route.fulfill({json})
 })
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message))
 return {context,page,calls,errors}
}

const pages=[
 ['subscription orders','/orders?business=subscription','/api/orders',false],
 ['refund orders','/orders?business=refund','/api/refund/orders',false],
 ['customer activity','/activity','/api/activity',false],
 ['API keys','/keys','/api/keys',false],
 ['wallet ledger','/wallet','/api/wallet/ledger',false],
 ['renewal tasks','/renewal','/api/renewals',false],
 ['partners','/invites','/api/invites',false],
 ['operator subscription orders','/admin/orders?business=subscription','/api/admin/orders',true],
 ['operator refund orders','/admin/orders?business=refund','/api/admin/refund/orders',true],
 ['operator activity','/admin/activity','/api/admin/activity',true],
 ['accounts','/admin/users','/api/admin/users',true],
 ['recharges','/admin/topups','/api/admin/topups',true],
 ['redeem codes','/admin/codes','/api/admin/codes',true],
 ['audit','/admin/audit','/api/admin/audit',true],
 ['operator invitations','/admin/invites','/api/admin/invites',true],
 ['manual subscription proxies','/admin/resources','/api/admin/resources',true],
 ['mail deliveries','/admin/settings','/api/admin/mail/logs',true,'邮件设置'],
 ['refund mothers','/admin/refund','/api/admin/refund/resources/mothers',true],
 ['refund proxies','/admin/refund','/api/admin/refund/resources/proxies',true,'代退代理'],
 ['registration orders','/orders?business=registration','/registration/api/user/v1/registration-orders',false],
 ['registration accounts','/accounts','/registration/api/user/v1/account-orders',false],
 ['operator registration orders','/admin/orders?business=registration','/registration/api/admin/v1/orders',true],
 ['operator delivered accounts','/admin/accounts','/registration/api/admin/v1/account-orders',true],
]

test('both portals paginate, sort and restore all primary record lists',{timeout:240000},async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true})
 const failures=[]
 try{
  for(const [name,route,endpoint,admin,tab] of pages){
   const {context,page,calls,errors}=await harness(browser,admin)
   try{
    await page.goto('http://127.0.0.1:18970/#'+route)
    if(tab)await page.getByRole(tab==='代退代理'?'tab':'button',{name:tab,exact:true}).click()
    const table=page.locator('main table:visible').first(),pager=page.locator('main select[aria-label="每页条数"]:visible').first()
    const grouped=name==='operator delivered accounts',records=grouped?page.locator('.p-admin-delivery-order'):table.locator('tbody tr')
    await records.nth(9).waitFor()
    for(const size of [10,50,100,500]){
     await pager.selectOption(String(size));await records.nth(size-1).waitFor();assert.equal(await records.count(),size,name)
    }
    await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname===endpoint&&(new URL(r.url()).searchParams.get('page')==='2'||new URL(r.url()).searchParams.get('offset')==='500')),page.locator('main').getByRole('button',{name:'下一页',exact:true}).first().click()])
    await records.nth(25).waitFor({state:'detached'});assert.equal(await records.count(),25)
    await pager.selectOption('50');await records.nth(49).waitFor()
    const heads=grouped?page.getByLabel('交付订单排序').locator('.list-sort-heading'):table.locator('thead .list-sort-heading')
    assert.ok(await heads.count()>0,name+' sorting missing')
    for(let n=0;n<2;n++){
     const done=page.waitForResponse(r=>new URL(r.url()).pathname===endpoint)
     await heads.first().click();await done;await pager.isEnabled()
    }
    await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname===endpoint&&(new URL(r.url()).searchParams.get('page')==='2'||new URL(r.url()).searchParams.get('offset')==='50')),page.locator('main').getByRole('button',{name:'下一页',exact:true}).first().click()])
    await expect(page.locator('main .list-pagination:visible,main .p-pagination:visible').first()).toContainText('2 / 11')
    const matching=c=>c.p===endpoint&&(name!=='wallet ledger'||c.params.business==='shared')
    const before=calls.filter(matching).at(-1).params
    assert.equal(before.page_size||before.limit,'50',name)
    await page.reload();await records.nth(49).waitFor()
    const after=calls.filter(matching).at(-1).params
    for(const key of ['page_size','limit','page','offset','sort_by','sort_dir'])assert.equal(after[key],before[key],`${name}: ${key}`)
    await page.setViewportSize({width:390,height:844})
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,name+' mobile overflow')
    const search=name.endsWith('activity')?page.locator('.activity-search input'):name==='wallet ledger'?page.locator('.ledger-panel input[type="search"]').first():page.locator('main input[placeholder^="搜索"]:visible').first()
    await search.waitFor()
    await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname===endpoint&&new URL(r.url()).searchParams.get('q')==='member001'),search.fill('member001').then(()=>search.press('Enter'))])
    await expect(records).toHaveCount(1)
    await page.reload();await expect(records).toHaveCount(1)
    assert.equal(calls.filter(matching).at(-1).params.q,'member001',name+' search restore')
    assert.deepEqual(errors,[],name)
    console.log('list passed: '+name)
   }catch(error){failures.push(name+': '+error.message);console.error(failures.at(-1))}finally{await context.close()}
  }
  assert.deepEqual(failures,[])
 }finally{await browser.close()}
})

test('registration account search and service filters survive refresh',{timeout:60000},async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true})
 const {context,page,calls}=await harness(browser,false)
 try{
  await page.goto('http://127.0.0.1:18970/#/accounts')
  await page.getByLabel('搜索',{exact:true}).fill('member00')
  await page.getByRole('button',{name:'查询',exact:true}).click()
  await page.getByLabel('筛选服务',{exact:true}).selectOption('oauth')
  await page.waitForResponse(r=>r.url().includes('/account-orders?')&&new URL(r.url()).searchParams.get('mode')==='oauth')
  await page.reload();await page.locator('main table tbody tr').first().waitFor()
  const params=calls.filter(c=>c.p==='/registration/api/user/v1/account-orders').at(-1).params
  assert.equal(params.q,'member00');assert.equal(params.mode,'oauth')
  assert.equal(await page.getByLabel('搜索',{exact:true}).inputValue(),'member00')
  assert.equal(await page.getByLabel('筛选服务',{exact:true}).inputValue(),'oauth')
  for(const id of ['order002','order003']){
   await page.goto('http://127.0.0.1:18970/#/accounts?order_id='+id)
   await page.getByRole('button',{name:'展开订单 '+id,exact:true}).waitFor()
   const query=calls.filter(c=>c.p==='/registration/api/user/v1/account-orders').at(-1).params
   assert.equal(query.order_id,id);assert.equal(query.q,'');assert.equal(query.mode,'')
  }
  await page.goBack();await page.getByRole('button',{name:'展开订单 order002',exact:true}).waitFor()
  await page.reload();await page.getByRole('button',{name:'展开订单 order002',exact:true}).waitFor()
 }finally{await context.close();await browser.close()}
})
