import {test} from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import {chromium} from '@playwright/test'

test('delete bound and global refund proxies without a usage veto',async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true})
 const context=await browser.newContext({viewport:{width:1440,height:1000}})
 context.setDefaultTimeout(10000)
 const root=path.resolve('../webui'),writes=[],errors=[]
 const data={runtime_ready:true,settings:{enabled:true,max_parallel:2,max_attempts:2,retry_seconds:30,mother_interval_seconds:0,child_proxy_id:'global',mother_failover:{enabled:true,max_switches:3,retry_seconds:30},recovery:{enabled:true,max_retries:3,verify_intervals:[30],cleanup_intervals:[15],alert_seconds:600,cleanup_failed:true}},proxies:[{id:'global',name:'全局代理',enabled:true},{id:'mother-proxy',name:'母号代理',enabled:true}],mothers:[{id:'mother',name:'测试母号',email:'mother@example.test',team_id:'offline-team',seat_type:'default',proxy_id:'mother-proxy',enabled:true}]}
 await context.route('**/*',async route=>{
  const req=route.request(),p=new URL(req.url()).pathname
  if(p==='/'||p.startsWith('/assets/'))return route.fulfill({path:path.join(root,p==='/'?'index.html':p.slice(1))})
  if(req.method()==='DELETE'){
   writes.push(p);const id=p.split('/').at(-1);data.proxies=data.proxies.filter(x=>x.id!==id);if(data.settings.child_proxy_id===id)data.settings.child_proxy_id=''
   return route.fulfill({json:{ok:true}})
  }
  assert.equal(req.method(),'GET')
  const json=p==='/api/public'?{site:{name:'星桥',english_name:'StarBridge'},features:{},announcements:[],products:[]}:p==='/api/me'?{id:'admin',name:'测试管理员',email:'admin@example.test',role:'admin',features:{},navigation:[],custom_menus:[]}:p==='/api/admin/refund/settings'?data:{items:[]}
  return route.fulfill({json})
 })
 try{
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message))
  await page.goto('http://127.0.0.1:18970/#/admin/refund');await page.getByRole('tab',{name:'代退代理',exact:true}).click()
  for(const name of ['全局代理','母号代理']){
   const row=page.getByRole('row').filter({hasText:name}),button=row.getByRole('button',{name:'删除',exact:true})
   assert.equal(await button.isEnabled(),true)
   await button.click();const dialog=page.getByRole('dialog');await dialog.waitFor()
   assert.equal((await dialog.innerText()).includes('不能删除'),false)
   await dialog.getByRole('button',{name:'确认删除',exact:true}).click();await dialog.waitFor({state:'hidden'});assert.equal(await row.count(),0)
  }
  assert.deepEqual(writes,['/api/admin/refund/proxies/global','/api/admin/refund/proxies/mother-proxy'])
  await page.getByRole('tab',{name:'母号资源',exact:true}).click();await page.getByRole('button',{name:'编辑',exact:true}).click()
  assert.equal(await page.getByRole('combobox',{name:/^母号专属代理/}).inputValue(),'')
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false)
  assert.deepEqual(errors,[])
 }finally{await browser.close()}
})
