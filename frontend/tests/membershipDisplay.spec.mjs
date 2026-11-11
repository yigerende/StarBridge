import {test} from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import {mkdirSync} from 'node:fs'
import {chromium} from '@playwright/test'

test('assigned and earned membership share growth, next tier and pricing',async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true}),context=await browser.newContext({viewport:{width:1440,height:1000}})
 const root=path.resolve('../webui'),output=path.resolve('../.local/membership-display');mkdirSync(output,{recursive:true})
 const tiers=[{code:'L1',name:'启程会员',threshold:0,discount:100},{code:'L2',name:'银阶会员',threshold:2000000,discount:95},{code:'L3',name:'金阶会员',threshold:5000000,discount:90},{code:'L4',name:'铂阶会员',threshold:10000000,discount:85}]
 let source='manual',until=null,earned=false;const errors=[],writes=[]
 const membership=()=>({current:source==='manual'?(earned?tiers[3]:tiers[2]):tiers[0],automatic:source==='manual'?(earned?tiers[3]:tiers[2]):tiers[0],source,until,tiers,spent:source==='manual'?(earned?10000000:5000000):0,settled_spent:earned?5000000:0,growth_credit:source==='manual'?5000000:0,remaining:source==='manual'?(earned?0:5000000):2000000,progress:earned?100:0,next:source==='manual'?(earned?null:tiers[3]):tiers[1],fee_override:null})
 await context.route('**/*',route=>{
  const req=route.request(),p=new URL(req.url()).pathname
  if(p==='/'||p.startsWith('/assets/'))return route.fulfill({path:path.join(root,p==='/'?'index.html':p.slice(1))})
  if(req.method()!=='GET')writes.push(p)
  let json={items:[]}
  if(p==='/api/public')json={site:{name:'星桥'},features:{},announcements:[],products:[]}
  if(p==='/api/me')json={id:'customer',role:'member',name:'测试',balance:0,features:{},navigation:[],membership:membership()}
  if(p==='/api/membership')json={membership:membership(),services:[{id:'registration_standard',business:'registration',name:'标准账号',fee:500,member_fee:450,saved:50,price_source:'membership',membership_eligible:true,enabled:true,unit_label:'个'},{id:'registration_oauth',business:'registration',name:'OAuth 账号',fee:800,member_fee:800,saved:0,price_source:'standard',membership_eligible:false,enabled:true,unit_label:'个'},{id:'plus',business:'subscription',unit_label:'次',name:'Plus',fee:1000,member_fee:source==='manual'?900:1000,saved:source==='manual'?100:0,price_source:'membership',membership_eligible:true,enabled:true},{id:'refund',business:'refund',unit_label:'个',name:'代退办理',fee:500,member_fee:0,saved:500,price_source:'account_override',membership_eligible:true,enabled:true}],growth:[]}
  return route.fulfill({json})
 })
 try{
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message))
  await page.goto('http://127.0.0.1:18970/#/membership');await page.getByRole('progressbar',{name:'会员升级进度'}).waitFor()
  assert.match(await page.locator('.membership-growth').innerText(),/50,000.00/);assert.match(await page.locator('.membership-growth').innerText(),/升级至 铂阶会员/);assert.doesNotMatch(await page.locator('.membership-growth').innerText(),/银阶/)
  assert.match(await page.locator('.membership-tier.current').innerText(),/金阶会员/);assert.match(await page.locator('tbody').first().innerText(),/9.00/);assert.deepEqual(await page.locator('tbody tr td:first-child').allTextContents(),['注册','注册','订阅','代退']);assert.equal(await page.locator('.version-badge,.system-update').count(),0);await page.getByText('此服务不参与会员减免',{exact:true}).waitFor();assert.match(await page.locator('tbody tr').last().innerText(),/专属价格/)
  for(const width of [1440,390]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await page.screenshot({path:path.join(output,`assigned-${width}.png`)})}
  earned=true;await page.getByRole('button',{name:'刷新等级'}).click();await page.locator('.membership-intro').getByRole('heading',{name:'铂阶会员'}).waitFor();await page.getByText('已达到最高等级',{exact:true}).waitFor()
  until='2099-12-01T00:00:00Z';await page.getByRole('button',{name:'刷新等级'}).click();await page.getByText(/本次成长调整有效至/).waitFor()
  source='automatic';until=null;earned=false;await page.getByRole('button',{name:'刷新等级'}).click();await page.locator('.membership-intro').getByRole('heading',{name:'启程会员'}).waitFor();assert.match(await page.locator('.membership-growth').innerText(),/升级至 银阶会员/)
  assert.deepEqual(errors,[]);assert.deepEqual(writes,[])
 }finally{await browser.close()}
})
