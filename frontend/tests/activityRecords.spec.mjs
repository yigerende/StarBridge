import {test} from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import {mkdirSync} from 'node:fs'
import {chromium} from '@playwright/test'

test('operator overview opens full records in place; customer keeps summary',async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true}),ctx=await browser.newContext({viewport:{width:1440,height:1000}})
 const root=path.resolve('../webui'),output=path.resolve('../.local/activity-records');mkdirSync(output,{recursive:true})
 const requests=[],errors=[],writes=[];let role='admin',missing=false
 const user={id:'fixture-user',email:'fixture@example.test',role:'admin',name:'测试',balance:0,frozen:0}
 const date='2026-11-11T00:00:00Z'
 const records=['registration','subscription','refund','renewal'].map((business,i)=>({id:'record-'+i,business:business==='renewal'?'subscription':business,workflow:business==='renewal'?'renewal':'subscription',title:'记录 '+i,email:'fixture@example.test',owner:'owner@example.test',status:business==='registration'?'completed':business==='renewal'?'disabled':'succeeded',state:'completed',activation:'active',renewal:'disabled',quantity:1,delivered:1,fee:100,settled:100,frozen:0,created_at:date,updated_at:date,href:'#/orders?order_id=record-'+i,timing:{total_seconds:70,ongoing:false}}))
 await ctx.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname;requests.push(p);if(p.endsWith('/activity')){const today=new Date(Date.now()+8*3600000).toISOString().slice(0,10);assert.equal(url.searchParams.get('start'),today);assert.equal(url.searchParams.get('end'),today)}
  if(p==='/'||p.startsWith('/assets/'))return route.fulfill({path:path.join(root,p==='/'?'index.html':p.slice(1))})
  if(!p.startsWith('/api/')&&!p.startsWith('/registration/api/'))return route.fulfill({status:404,body:''})
  if(req.method()!=='GET'&&p!=='/api/announcements/query'){writes.push(p);return route.fulfill({json:{ok:true}})}
  let json={items:[],counts:{},maxParallel:10,available:10}
  if(p==='/api/public')json={site:{name:'星桥'},products:[],countries:[],features:{},announcements:[],registration_enabled:true}
  if(p==='/api/me'||p==='/registration/api/user/v1/me')json={...user,role,navigation:[{id:'activity',path:'/activity',label:'办理总览',group:'main'}]}
  if(p==='/registration/api/public')json={features:{}}
  if(p.endsWith('/activity'))json={items:records,total:60,has_more:true,summary:{total:60,completed:60,settled:400},businesses:[],show_fees:true}
  if(p==='/api/admin/orders/record-1'){
   if(missing)return route.fulfill({status:404,json:{error:'订单不存在'}})
   json={order:{...records[1],product_name:'Plus',note:''},payment:{state:'succeeded',fee_state:'charged'},events:[],trace:[],billing:{state:'not_selected'},checkout:{}}
  }
  if(p==='/api/admin/refund/orders/record-2')json={order:{...records[2],fee_status:'settled'},events:[],mother:{id:'mother-fixture',name:'测试母号',email:'mother@example.test'}}
  if(p==='/api/admin/refund/orders/record-2/timeline')json={items:[],active:false,generated_at:date}
  if(p==='/registration/api/admin/v1/orders/record-0')json={...records[0],items:[],unit_price:1,charged:1,released:0,attempts:[]}
  if(p==='/api/admin/renewals/record-3')json={task:{...records[3],message:'已关闭自动续费'},events:[{status:'disabled',message:'关闭结果已核实',created_at:date}]}
  return route.fulfill({json})
 })
 try{
  const page=await ctx.newPage();page.on('pageerror',e=>errors.push(e.message))
  await page.goto('http://127.0.0.1:18970/#/admin/activity');await page.getByRole('button',{name:'查看',exact:true}).first().waitFor()
  await page.getByRole('button',{name:'下一页',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.activity-records .list-pagination')?.textContent.includes('2 / 6'))
  for(const [index,title,marker] of [[0,'订单详情','已交付 1 / 1'],[1,'订单完整记录','办理阶段'],[2,'代退订单详情','办理流程'],[3,'续费任务完整记录','关闭结果已核实']]){
   await page.getByRole('button',{name:'查看',exact:true}).nth(index).click();const dialog=page.getByRole('dialog',{name:title,exact:true});await dialog.getByText(marker,{exact:false}).first().waitFor()
   if(index===2)await dialog.getByText('mother@example.test',{exact:true}).waitFor();
   assert.equal(new URL(page.url()).hash,'#/admin/activity');assert.equal(await page.getByRole('heading',{name:'订单中心',exact:true}).count(),0)
   for(const width of [1440,390]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await page.screenshot({path:path.join(output,`${index}-${width}.png`)})}
   await dialog.getByRole('button',{name:/^关闭(窗口)?$/}).click();await dialog.waitFor({state:'hidden'});assert.match(await page.locator('.activity-records .list-pagination').innerText(),/2 \/ 6/)
  }
  assert.ok(!requests.some(p=>['/api/admin/orders','/api/admin/refund/orders','/registration/api/admin/v1/orders'].includes(p)))
  missing=true;await page.getByRole('button',{name:'查看',exact:true}).nth(1).click();await page.getByRole('dialog').getByRole('alert').waitFor();await page.getByRole('dialog').getByRole('button',{name:'关闭窗口'}).click()
  role='member';await page.goto('http://127.0.0.1:18970/#/activity');await page.getByRole('button',{name:'查看',exact:true}).nth(1).click()
  const summary=page.getByRole('dialog',{name:'办理记录',exact:true});await summary.getByRole('link',{name:'查看完整记录'}).waitFor();assert.equal(await page.getByRole('dialog',{name:'订单完整记录',exact:true}).count(),0)
  assert.equal(await summary.getByText('mother@example.test',{exact:true}).count(),0);assert.equal(await page.getByRole('button',{name:'今天',exact:true}).getAttribute('aria-pressed'),'true');assert.deepEqual(errors,[]);assert.deepEqual(writes,[])
 }finally{await browser.close()}
})
