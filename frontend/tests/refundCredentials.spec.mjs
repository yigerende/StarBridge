import {test} from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import {mkdirSync} from 'node:fs'
import {chromium} from '@playwright/test'

test('administrator order credentials reveal, copy, hide and customer isolation',async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true})
 const ctx=await browser.newContext({viewport:{width:1440,height:1000},permissions:['clipboard-read','clipboard-write']})
 const root=path.resolve('../webui'),out=path.resolve('../.local/refund-credentials');mkdirSync(out,{recursive:true})
 const calls=[],errors=[],stamp='2026-11-16T09:48:28Z'
 let role='admin',fail=false
 const account={email:'target@example.test',password:'Sample<&> Very long password 中文 123456789012345678901234567890',totp_secret:'JBSWY3DPEHPK3PXP'}
 const order={id:'fixture-refund',email:account.email,status:'succeeded',message:'退款服务已完成',fee:7000,fee_status:'settled',created_at:stamp,updated_at:stamp}
 await ctx.route('**/*',async route=>{
  const p=new URL(route.request().url()).pathname;calls.push(p)
  if(p==='/'||p.startsWith('/assets/'))return route.fulfill({path:path.join(root,p==='/'?'index.html':p.slice(1))})
  let json={items:[],total:0,counts:{}}
  if(p==='/api/public')json={site:{name:'星桥'},products:[],countries:[],features:{},announcements:[]}
  if(p==='/api/me')json={id:'fixture-user',email:'operator@example.test',name:'测试',role,navigation:[],features:{}}
  if(p.endsWith('/refund/orders'))json={items:[order],total:1,summary:{succeeded:1}}
  if(p.endsWith('/refund/orders/'+order.id))json={order,events:[],mother:{name:'母号 5',email:'mother@example.test'}}
  if(p.endsWith('/timeline'))json={items:[],active:false,generated_at:stamp}
  if(p.endsWith('/credentials'))return route.fulfill({status:fail?409:200,json:fail?{error:'账号资料无法读取，请核对原始提交记录'}:account})
  if(p.endsWith('/activity'))json={items:[{...order,business:'refund',title:'代退办理',state:'completed',quantity:1,settled:7000}],total:1,summary:{total:1,completed:1,settled:7000},show_fees:true}
  return route.fulfill({json})
 })
 try{
  const page=await ctx.newPage();page.on('pageerror',e=>errors.push(e.message))
  await page.goto('http://127.0.0.1:18970/#/admin/orders?business=refund&order_id='+order.id)
  let dialog=page.getByRole('dialog',{name:'代退订单详情',exact:true})
  await dialog.getByRole('button',{name:'查看密码和 2FA',exact:true}).waitFor()
  assert.equal(calls.filter(p=>p.endsWith('/credentials')).length,0)
  await dialog.getByRole('button',{name:'查看密码和 2FA',exact:true}).click()
  await dialog.getByText(account.password,{exact:true}).waitFor()
  for(const [label,value] of [['复制密码',account.password],['复制2FA 密钥',account.totp_secret]]){
   await dialog.getByRole('button',{name:label,exact:true}).click()
   assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),value)
  }
  for(const width of [1440,390]){
   await page.setViewportSize({width,height:1000})
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false)
   await page.screenshot({path:path.join(out,`credentials-${width}.png`)})
  }
  assert.ok(!(await page.evaluate(()=>JSON.stringify([localStorage,sessionStorage]))).includes(account.password))
  await dialog.getByRole('button',{name:'隐藏资料',exact:true}).click()
  assert.equal(await dialog.getByText(account.password,{exact:true}).count(),0)
  await dialog.getByRole('button',{name:'查看密码和 2FA',exact:true}).click();await dialog.getByText(account.password,{exact:true}).waitFor()
  await dialog.getByRole('button',{name:'关闭',exact:true}).click()
  await page.getByRole('button',{name:'查看详情',exact:true}).click()
  await dialog.getByRole('button',{name:'查看密码和 2FA',exact:true}).waitFor()
  assert.equal(await dialog.getByText(account.password,{exact:true}).count(),0)
  fail=true
  await dialog.getByRole('button',{name:'查看密码和 2FA',exact:true}).click()
  await dialog.getByRole('alert').filter({hasText:'账号资料无法读取'}).waitFor()
  assert.equal(await dialog.getByText(account.totp_secret,{exact:true}).count(),0)
  fail=false
  await page.goto('http://127.0.0.1:18970/#/admin/activity')
  await page.getByRole('button',{name:'查看',exact:true}).click()
  dialog=page.getByRole('dialog',{name:'代退订单详情',exact:true})
  await dialog.getByRole('button',{name:'查看密码和 2FA',exact:true}).click()
  await dialog.getByText(account.password,{exact:true}).waitFor()
  const reads=calls.filter(p=>p.endsWith('/credentials')).length
  role='member'
  await page.goto('http://127.0.0.1:18970/#/orders?business=refund&order_id='+order.id)
  dialog=page.getByRole('dialog',{name:'代退订单详情',exact:true})
  await dialog.getByRole('heading',{name:'服务已完成',exact:true}).waitFor()
  assert.equal(await dialog.getByRole('button',{name:'查看密码和 2FA',exact:true}).count(),0)
  assert.equal(calls.filter(p=>p.endsWith('/credentials')).length,reads)
  assert.equal(await page.getByText(account.password,{exact:true}).count(),0)
  assert.deepEqual(errors,[])
  console.log(JSON.stringify({adminOrders:true,adminOverview:true,customerHidden:true,copyVerified:true,noBrowserStorage:true,desktop:1440,mobile:390,browserErrors:0}))
 }finally{await browser.close()}
})
