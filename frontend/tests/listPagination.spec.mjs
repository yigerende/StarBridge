import {test} from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import {chromium} from '@playwright/test'

test('paged growth, membership, countries, profiles and announcements keep controls on refresh',async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true}),ctx=await browser.newContext({viewport:{width:1440,height:1000}});ctx.setDefaultTimeout(12000)
 const root=path.resolve('../webui'),calls=[],errors=[],writes=[]
 const tier={code:'base',name:'普通会员',threshold:0,discount:100},membership={current:tier,tiers:[tier],spent:100,remaining:0,progress:100,source:'automatic',until:null,fee_override:null,product_prices:{}}
 const rows=Array.from({length:525},(_,i)=>({id:`row${String(i).padStart(3,'0')}`,email:`member${String(i).padStart(3,'0')}@example.test`,name:`Row ${String(i).padStart(3,'0')}`,role:'member',status:'active',balance:100,frozen:0,used_balance:0,membership,product_prices:{},product_name:'Product '+i,spent:i,updated_at:'2026-11-12T00:00:00Z',created_at:'2026-11-12T00:00:00Z',code:`C${String(i).padStart(3,'0')}`,currency:'USD',enabled:true,sort:i,proxy_available:true,flow_supported:true,pool_supported:true,first_name:'Test',last_name:'Customer',line1:'1 Fixture Street',city:'City',state:'CA',postal_code:'90001',test_only:true,title:'Notice '+String(i).padStart(3,'0'),body:'Body '+i,version:String(i).padStart(32,'0')}))
 function paging(params,source=rows){const page=Number(params.page||1),size=Number(params.page_size||10),q=params.q||'',field=params.sort_by||'id',direction=params.sort_dir==='desc'?-1:1;let result=source.filter(r=>JSON.stringify(r).includes(q));result=[...result].sort((a,b)=>String(a[field]??a.id).localeCompare(String(b[field]??b.id),undefined,{numeric:true})*direction);return {items:result.slice((page-1)*size,page*size),total:result.length,page,page_size:size,has_more:page*size<result.length}}
 await ctx.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname,params=Object.fromEntries(url.searchParams),body=req.method()!=='GET'?req.postDataJSON():null
  if(p==='/'||p.startsWith('/assets/'))return route.fulfill({path:path.join(root,p==='/'?'index.html':p.slice(1))})
  calls.push({p,params});if(body)writes.push({p,body})
  let json={items:[],total:0,index:[]}
  if(p==='/api/public')json={site:{name:'星桥'},features:{},products:[],countries:[],announcements:[]}
  if(p==='/api/me')json={id:'admin',name:'管理员',email:'admin@example.test',role:'admin',status:'active',membership,navigation:[],features:{},custom_menus:[]}
  if(p==='/api/membership'){const data=paging(params);json={...data,growth:data.items,membership,products:[],services:[]}}
  if(p==='/api/membership/services')json={...paging(params,rows.map(r=>({...r,business:'subscription',fee:r.spent,member_fee:r.spent,price_source:'membership',membership_eligible:true,unit_label:'次'}))),discount:100}
  if(p==='/api/admin/membership')json={customers:525,config:{items:[tier]},distribution:{base:525}}
  if(p==='/api/admin/users')json={...paging(params),tiers:[tier],products:[]}
  if(p==='/api/admin/settings')json={proxy:{mode:'manual'},billing:{address_templates:[]}}
  if(p==='/api/admin/billing-profiles')json={...paging(params,rows.slice(0,61)),summary:{total:61,enabled:61,payment:0}}
  if(p==='/api/admin/regions'){const countries=rows.slice(0,211);json={...(params.page_size?paging(params,countries):{items:countries}),all_index:countries.map(r=>({code:r.code,enabled:r.enabled})),filtered_index:countries,currencies:['USD'],proxy_mode:'manual'}}
  if(p==='/api/admin/resources')json={...paging(params,[])}
  if(p==='/api/admin/country-pool')json={settings:{auth_type:'credentials',renewal_country:'C000',lifetime_seconds:3600,max_parallel:20,auto_sync:true},countries:rows.slice(0,211),mode:'manual',in_use:0}
  if(p==='/api/admin/country-pool/countries')json=paging(params,rows.slice(0,211))
  if(p==='/api/announcements/query'){const all=rows.slice(0,161);json={...paging(body,body.unread?all.filter(r=>body.seen[r.id]!==r.version):all),index:all.map(r=>({id:r.id,version:r.version}))}}
  if(p.startsWith('/api/admin/billing-profiles/')&&req.method()==='PUT')json={ok:true}
  return route.fulfill({json})
 })
 const page=await ctx.newPage();page.on('pageerror',e=>errors.push(e.message))
 async function exercise(selector,endpoint,total){
  const area=page.locator(selector),pager=area.getByLabel('每页条数',{exact:true}),table=area.locator('table').last()
  for(const size of [10,50,100,500]){await pager.selectOption(String(size));await page.waitForFunction(({selector,count})=>document.querySelectorAll(selector+' table:last-of-type tbody tr').length===count,{selector,count:Math.min(size,total)}).catch(async()=>{await table.locator('tbody tr').nth(Math.min(size,total)-1).waitFor()});assert.equal(await table.locator('tbody tr').count(),Math.min(size,total))}
  if(total>500){await area.getByRole('button',{name:'下一页',exact:true}).click();await page.waitForFunction(selector=>document.querySelectorAll(selector+' table:last-of-type tbody tr').length===25,selector).catch(async()=>{await table.locator('tbody tr').nth(24).waitFor()});assert.equal(await table.locator('tbody tr').count(),25)}
  const heads=table.locator('thead button.list-sort-heading')
  for(let i=0;i<await heads.count();i++){
   for(let n=0;n<2;n++){const response=page.waitForResponse(r=>new URL(r.url()).pathname===endpoint&&new URL(r.url()).searchParams.has('sort_by'));await heads.nth(i).click();await response;await page.waitForTimeout(50)}
  }
  await pager.selectOption('50');await table.locator('tbody tr').nth(49).waitFor()
  const before=calls.filter(c=>c.p===endpoint&&c.params.page_size).at(-1).params
  await page.reload();await page.locator(selector).locator('table').last().locator('tbody tr').nth(49).waitFor()
  const after=calls.filter(c=>c.p===endpoint&&c.params.page_size).at(-1).params
  assert.equal(after.page_size,'50');assert.equal(after.sort_by,before.sort_by);assert.equal(after.sort_dir,before.sort_dir)
 }
 try{
  await page.goto('http://127.0.0.1:18970/#/membership');await page.getByRole('heading',{name:'成长明细',exact:true}).waitFor()
  await exercise('.membership-page > .panel:last-child','/api/membership',525)
  await page.getByLabel('搜索成长明细').fill('row524');await page.getByText('row524',{exact:true}).waitFor();await page.waitForTimeout(350);assert.equal(await page.locator('.membership-page > .panel:last-child tbody tr').count(),1)
  await exercise('.membership-quotes','/api/membership/services',525)
  await page.getByLabel('搜索服务报价').fill('Row 524');await page.getByText('Row 524',{exact:true}).waitFor();await page.waitForTimeout(350);assert.equal(await page.locator('.membership-quotes tbody tr').count(),1)
  await page.reload();await page.getByText('Row 524',{exact:true}).waitFor();assert.equal(await page.getByLabel('搜索服务报价').inputValue(),'Row 524')
  await page.goto('http://127.0.0.1:18970/#/admin/membership');await page.getByLabel('搜索会员').waitFor()
  await exercise('main .panel:has(input[aria-label="搜索会员"])','/api/admin/users',525)
  await page.getByRole('checkbox',{name:'包含管理员',exact:true}).check();await page.waitForTimeout(350);assert.equal(calls.filter(c=>c.p==='/api/admin/users').at(-1).params.role,'')
  await page.goto('http://127.0.0.1:18970/#/admin/billing');await page.getByRole('heading',{name:'资料库',exact:true}).waitFor()
  await exercise('.billing-manager','/api/admin/billing-profiles',61)
  await page.locator('.billing-manager').getByRole('button',{name:'编辑',exact:true}).first().click();await page.getByRole('dialog').getByRole('button',{name:'保存订阅资料',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'})
  assert.equal(writes.filter(w=>w.p.startsWith('/api/admin/billing-profiles/')).length,1)
  await page.goto('http://127.0.0.1:18970/#/admin/regions');await page.locator('main table tbody tr').nth(9).waitFor()
  await exercise('main .panel:has(table)','/api/admin/regions',211)
  await page.goto('http://127.0.0.1:18970/#/admin/resources');await page.getByRole('button',{name:'独立会话代理',exact:true}).click();await page.getByRole('heading',{name:'国家与连接测试',exact:true}).waitFor()
  await exercise('.pool-layout .panel:has(table)','/api/admin/country-pool/countries',211)
  await page.getByRole('button',{name:/^公告，/}).click();let modal=page.getByRole('dialog',{name:'通知公告'})
  await modal.getByLabel('每页条数').selectOption('50');await modal.locator('.announcement-item').nth(49).waitFor()
  await modal.getByRole('button',{name:'下一页',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('.announcement-item').length===50)
  await modal.getByRole('button',{name:'全部标为已读',exact:true}).click();await modal.getByRole('button',{name:'未读（0）',exact:true}).click();await modal.getByText('未读公告已全部处理',{exact:true}).waitFor()
  await modal.getByRole('button',{name:'关闭窗口',exact:true}).click();await page.reload();await page.getByRole('button',{name:'公告，0 条未读',exact:true}).waitFor()
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false)
  assert.deepEqual(errors,[]);console.log(JSON.stringify({lists:6,pageSizes:[10,50,100,500],refreshPreserved:true,announcementVersionRead:true,browserErrors:errors.length}))
 }finally{await browser.close()}
})
