import {test} from 'node:test'
import assert from 'node:assert/strict'
import {fileURLToPath} from 'node:url'
import {readFile,mkdir} from 'node:fs/promises'
import {createServer} from 'vite'
import {chromium} from '@playwright/test'

const root=fileURLToPath(new URL('../',import.meta.url))
test('both StarBridge credential dialogs display, copy and download complete Session JSON', {timeout:90000},async()=>{
 const server=await createServer({root,cacheDir:root+'../.local/session-vite',server:{host:'127.0.0.1',port:19027,strictPort:true}})
 let browser,context
 const errors=[]
 try{
  await server.listen()
  const base='http://127.0.0.1:'+server.httpServer.address().port
  browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_BROWSER_CHANNEL||'chrome'})
  context=await browser.newContext({permissions:['clipboard-read','clipboard-write'],acceptDownloads:true})
  for(const admin of [false,true]){
   const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message))
   await page.goto(base+'/tests/fixtures/registration-sessions.html'+(admin?'?admin=1':''))
   if(!admin)await page.getByRole('button',{name:'按账号',exact:true}).click()
   else await page.getByRole('button',{name:/订单 order-one.*owner@example.test/}).click()
   await page.getByRole('checkbox',{name:'选择 account@example.test',exact:true}).check()
   const downloadPromise=page.waitForEvent('download')
   await page.getByRole('button',{name:'邮箱+Session',exact:true}).click()
   const download=await downloadPromise
   assert.equal(download.suggestedFilename(),'starbridge-email-session.txt')
   const text=await readFile(await download.path(),'utf8')
   const expected=await page.evaluate(()=>window.fixtureSession)
   assert.deepEqual(JSON.parse(text.trim().split('----')[1]),expected)
   await page.getByRole('button',{name:'查看凭据',exact:true}).click()
   await page.getByRole('textbox',{name:'Session',exact:true}).waitFor()
   assert.deepEqual(JSON.parse(await page.getByRole('textbox',{name:'Session',exact:true}).inputValue()),expected)
   assert.equal(await page.getByText('完整 Session Token',{exact:true}).count(),0)
   await page.getByRole('button',{name:'复制Session',exact:true}).click()
   assert.deepEqual(JSON.parse(await page.evaluate(()=>navigator.clipboard.readText())),expected)
   await page.getByRole('button',{name:'展开Session',exact:true}).click()
   assert.deepEqual(JSON.parse(await page.getByRole('textbox',{name:'Session完整内容',exact:true}).inputValue()),expected)
   await page.setViewportSize({width:390,height:844})
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false)
   await mkdir(root+'../.local/session-screenshots',{recursive:true})
   await page.screenshot({path:root+'../.local/session-screenshots/'+(admin?'admin':'customer')+'.png'})
   await page.close()
  }
  const page=await context.newPage()
  await page.goto(base+'/tests/fixtures/registration-sessions.html?status=pending')
  await page.getByRole('button',{name:'按账号',exact:true}).click()
  await page.getByRole('button',{name:'查看凭据',exact:true}).click()
  await page.getByText('Session 正在获取，请稍后重新查看。',{exact:true}).waitFor()
  assert.equal(await page.getByRole('button',{name:'复制Session',exact:true}).isDisabled(),true)
  assert.deepEqual(errors,[])
 }catch(error){console.error(error);throw error}
 finally{if(context)await context.close();if(browser)await browser.close();await server.close()}
})
