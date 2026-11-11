type Order=Record<string,any>

// Cancellation is a separate result; only saved payment and activation evidence
// can make a subscription successful.
export function subscriptionConfirmed(order?:Order,payment?:Order){
 const state=payment?.state??order?.payment?.state
 return order?.workflow==='subscription'&&order.status==='succeeded'&&order.activation==='active'&&(!state||state==='succeeded')
}
export const renewalNeedsConfirmation=(order?:Order,payment?:Order)=>subscriptionConfirmed(order,payment)&&order?.renewal==='review'
export const renewalConfirmationNotice='续费处理暂未完成，不影响本次订阅结果。'
