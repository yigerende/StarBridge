import {test} from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {parseAccounts} from '../src/refund/import.ts'
test('old three-column and optional four-column imports preserve credentials',()=>{
 const legacy='one@example.test----pw----JBSWY3DPEHPK3PXP'
 assert.deepEqual(parseAccounts(legacy),[{email:'one@example.test',password:'pw',totp_secret:'JBSWY3DPEHPK3PXP'}])
 const url='https://mail.example.test/get-code?uid=fixture'
 assert.equal(parseAccounts(legacy+'----'+url)[0].email_code_url,url)
 assert.equal(parseAccounts('email,password,totp_secret,email_code_url\none@example.test,"a,b",JBSWY3DPEHPK3PXP,"'+url+'&extra=a,b"')[0].email_code_url,url+'&extra=a,b')
 assert.equal(parseAccounts('one@example.test\tpw\tJBSWY3DPEHPK3PXP\t'+url)[0].email_code_url,url)
 assert.deepEqual(parseAccounts(legacy+'----'),parseAccounts(legacy))
 assert.throws(()=>parseAccounts(legacy+'----file:///secret'),/地址无效/)
})
test('customer contract adds optional write-only URL without changing existing requirements',()=>{
 const spec=JSON.parse(readFileSync(new URL('../../backend/openapi.json',import.meta.url),'utf8'))
 const request=spec.paths['/api/v1/refund/orders'].post.requestBody.content['application/json']
 const account=request.schema.properties.accounts.items
 assert.deepEqual(account.required,['email','password','totp_secret'])
 assert.equal(account.properties.email_code_url.writeOnly,true)
 assert.equal(request.examples.with_email_code_url.value.accounts[0].email_code_url.startsWith('https://'),true)
})
