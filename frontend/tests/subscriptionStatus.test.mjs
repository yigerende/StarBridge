import {test} from 'node:test'
import assert from 'node:assert/strict'
import {subscriptionConfirmed,renewalNeedsConfirmation} from '../src/subscriptionStatus.ts'
const order={workflow:'subscription',status:'succeeded',activation:'active',renewal:'review'}
test('confirmed subscription succeeds independently of cancellation',()=>{
 for(const renewal of ['disabled','kept','pending','retrying','review'])assert.equal(subscriptionConfirmed({...order,renewal}),true)
 assert.equal(renewalNeedsConfirmation(order),true)
 assert.equal(renewalNeedsConfirmation({...order,renewal:'disabled'}),false)
})
test('payment or activation uncertainty cannot become subscription success',()=>{
 for(const activation of ['pending','review','not_checked','',undefined]){
  assert.equal(subscriptionConfirmed({...order,activation}),false)
  assert.equal(renewalNeedsConfirmation({...order,activation}),false)
 }
 for(const status of ['processing','review','failed','cancelled'])assert.equal(subscriptionConfirmed({...order,status}),false)
 for(const state of ['review','checking','failed'])assert.equal(subscriptionConfirmed(order,{state}),false)
 assert.equal(subscriptionConfirmed({...order,workflow:'link'}),false)
 assert.equal(subscriptionConfirmed(undefined),false)
})
