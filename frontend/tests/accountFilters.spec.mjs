import {test} from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import {mkdirSync} from 'node:fs'
import {chromium} from '@playwright/test'

test('account search, filters, pagination, reset and supplier session settings',async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true}),ctx=await browser.newContext({viewport:{width:1440,height:1000}})
 const root=path.resolve('../webui'),output=path.resolve('../.local/account-filters');mkdirSync(output,{recursive:true})
 const errors=[],queries=[],writes=[];ctx.setDefaultTimeout(10000)
 const tiers=[{code:'base',name:'普通会员',threshold:0,discount:100},{code:'gold',name:'金卡',threshold:1000,discount:90}]
 const user={id:'admin',email:'admin@example.test',name:'管理员',role:'admin',status:'active',balance:0,frozen:0}
 const accounts=Array.from({length:51},(_,i)=>({id:'account-'+i,email:`member${i}@example.test`,name:`客户 ${i}`,role:i===0?'admin':'member',status:i%2?'disabled':'active',membership:{current:tiers[i%2],source:'automatic'},created_at:'2026-11-11T00:00:00Z',balance:100,frozen:0,used_balance:12345}))
 const products=[{id:'plus',name:'Plus',fee:1000}]
 const regions=[{code:'PH',name:'菲律宾',currency:'PHP'},{code:'US',name:'美国',currency:'USD'}]
 await ctx.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname
  if(p==='/'||p.startsWith('/assets/'))return route.fulfill({path:path.join(root,p==='/'?'index.html':p.slice(1))})
  if(!p.startsWith('/api/')&&!p.startsWith('/registration/api/'))return route.fulfill({status:404,body:''})
  if(req.method()!=='GET'){writes.push(req.postDataJSON());return route.fulfill({json:{ok:true,id:'saved'}})}
  let json={items:[]}
  if(p==='/api/public')json={site:{name:'星桥'},countries:[regions[0]],products:[],features:{},announcements:[]}
  if(p==='/api/me')json={...user,navigation:[]}
  if(p==='/api/admin/users'){
   const args=Object.fromEntries(url.searchParams);queries.push(args)
   if(args.q==='slow')await new Promise(resolve=>setTimeout(resolve,800))
   let rows=accounts.filter(a=>(!args.q||`${a.name} ${a.email} ${a.id}`.includes(args.q))&&(!args.role||a.role===args.role)&&(!args.status||a.status===args.status)&&(!args.tier||a.membership.current.code===args.tier))
   const size=Number(args.page_size),page=Number(args.page)
   json={items:rows.slice((page-1)*size,page*size),total:rows.length,page,page_size:size,has_more:page*size<rows.length,tiers,products}
  }
  if(p==='/api/admin/settings')json={proxy:{mode:'manual'}}
  if(p==='/api/admin/regions')json={items:regions}
  return route.fulfill({json})
 })
 try{
  const page=await ctx.newPage();page.on('pageerror',e=>errors.push(e.message))
  await page.goto('http://127.0.0.1:18970/#/admin/users');await page.getByText('客户 0',{exact:true}).waitFor()
  assert.equal(await page.locator('tbody tr').count(),10)
  assert.match(await page.locator('thead').innerText(),/冻结余额[\s\S]*已用余额[\s\S]*会员等级/);assert.match(await page.locator('tbody tr').first().innerText(),/123.45 积分/)
  await page.getByLabel('选择本页',{exact:true}).check();await page.getByRole('button',{name:'下一页',exact:true}).click()
  await page.getByText('客户 10',{exact:true}).waitFor();assert.equal(await page.getByLabel('选择本页',{exact:true}).isChecked(),false)
  assert.deepEqual(await page.getByLabel('每页条数').locator('option').allTextContents(),['10','50','100','500'])
  await page.getByLabel('选择本页',{exact:true}).check();await page.getByRole('button',{name:'批量专属价格',exact:true}).click()
  const pricing=page.getByRole('dialog',{name:'批量调整专属价格'});await pricing.getByLabel('标准账号调整方式',{exact:true}).selectOption('set');await pricing.getByLabel('标准账号专属价格',{exact:true}).fill('1.23')
  await pricing.getByLabel('Plus调整方式',{exact:true}).selectOption('clear');await pricing.getByLabel('代退服务费调整方式',{exact:true}).selectOption('set');await pricing.getByLabel('代退服务费专属价格',{exact:true}).fill('0')
  await page.screenshot({path:path.join(output,'batch-pricing.png'),fullPage:true});await pricing.getByRole('button',{name:'确认保存',exact:true}).click();await pricing.waitFor({state:'hidden'})
  assert.equal(writes.at(-1).ids.length,10);assert.deepEqual(writes.at(-1).changes,{service_prices:{registration_standard:123,refund:0},product_prices:{plus:null}})
  await page.getByLabel('选择本页',{exact:true}).check();await page.getByRole('button',{name:'批量会员',exact:true}).click()
  const members=page.getByRole('dialog',{name:'批量调整会员'});await members.getByLabel('会员等级',{exact:false}).selectOption('gold');await members.getByRole('button',{name:'确认保存',exact:true}).click();await members.waitFor({state:'hidden'});assert.deepEqual(writes.at(-1).changes,{membership:{code:'gold',until:null}})
  await page.getByLabel('搜索账户',{exact:true}).fill('member50');await page.getByRole('button',{name:'查询',exact:true}).click()
  await page.getByText('客户 50',{exact:true}).waitFor();assert.equal(await page.locator('tbody tr').count(),1);assert.equal(queries.at(-1).page,'1')
  await page.getByRole('button',{name:'重置',exact:true}).click();await page.getByText('客户 0',{exact:true}).waitFor()
  await page.getByLabel('账户角色',{exact:true}).selectOption('member');await page.getByLabel('账户状态',{exact:true}).selectOption('disabled');await page.getByLabel('会员等级筛选',{exact:true}).selectOption('gold')
  await page.waitForFunction(()=>document.querySelector('.account-panel')?.getAttribute('aria-busy')==='false')
  assert.equal(queries.at(-1).tier,'gold');assert.equal(await page.locator('tbody tr').count(),10)
  await page.getByLabel('每页条数').selectOption('50');await page.waitForFunction(()=>document.querySelectorAll('tbody tr').length===25)
  for(const width of [1440,390]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await page.screenshot({path:path.join(output,`accounts-${width}.png`),fullPage:true})}
  await page.getByLabel('搜索账户').fill('slow');await page.getByRole('button',{name:'查询',exact:true}).click()
  await page.getByLabel('搜索账户').fill('member1');await page.getByLabel('账户状态').selectOption('')
  await page.getByText('客户 1',{exact:true}).waitFor();await page.waitForTimeout(1000);assert.ok(await page.getByText('客户 1',{exact:true}).isVisible())
  await page.getByLabel('搜索账户').fill('missing');await page.getByRole('button',{name:'查询',exact:true}).click();await page.getByText('没有符合条件的账户',{exact:true}).waitFor()
  await page.getByRole('button',{name:'重置',exact:true}).click();await page.getByText('客户 0',{exact:true}).waitFor()
  await page.goto('http://127.0.0.1:18970/#/admin/resources');await page.getByRole('button',{name:'添加资源',exact:true}).click()
  const dialog=page.getByRole('dialog');await dialog.getByLabel('资源名称',{exact:true}).fill('测试代理')
  await dialog.getByLabel('代理厂商',{exact:false}).selectOption('1024proxy');await dialog.getByLabel('粘性会话有效期（分钟）',{exact:false}).fill('25')
  await dialog.getByLabel('代理地址',{exact:false}).fill('http://fixture-region-PH-sid-old-t-5:password@gateway.example.test:3000')
  assert.equal(await dialog.getByLabel('订阅地区',{exact:false}).locator('option').count(),2)
  await page.screenshot({path:path.join(output,'manual-session-mobile.png'),fullPage:true})
  await dialog.getByRole('button',{name:'确认保存',exact:true}).click();await dialog.waitFor({state:'hidden'})
  assert.equal(writes.at(-1).session_minutes,25);assert.equal(writes.at(-1).vendor,'1024proxy')
  assert.deepEqual(errors,[])
 }finally{await browser.close()}
})
