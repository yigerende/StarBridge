<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {useRemoteList} from '../useRemoteList'
import {Search,ArrowUpRight,ArrowDownLeft,ArrowUpRight as Outgoing,LockKeyhole,RotateCcw,ReceiptText} from 'lucide-vue-next'
import {cash,date,labels,type Row} from '../lib'
import {deltaTone,signed,upstreamAmount,discountText} from '../record-format'
import RecordsTable from './RecordsTable.vue'
const props=defineProps<{rows:Row[];loading:boolean;revision?:number;business:string}>()
const shared=computed(()=>props.business==='shared')
const registration=computed(()=>props.business==='registration')
const searchLabel=computed(()=>shared.value?'流水编号 / 账户收支说明':registration.value?'注册订单号 / 流水说明':'订单号 / 邮箱 / 流水说明')
defineEmits<{order:[row:Row]}>()
const q=ref(''),kind=ref(''),start=ref(''),end=ref('')
const kinds=computed(()=>shared.value?['topup','redeem','adjustment','invite_reward','invite_reversal']:['freeze','unfreeze','charge','refund'])
const invalidDates=computed(()=>!!(start.value&&end.value&&start.value>end.value))
const hasFilters=computed(()=>!!(q.value||kind.value||start.value||end.value))
const {page,size,sortBy,sortDir,data,rows,loading,error,load,sort}=useRemoteList('ledger-'+props.business,()=>'/wallet/ledger',{q,kind,start,end},{extra:()=>({business:props.business})})
const filtered=rows
const totals=computed(()=>data.value.summary||{credit:0,spent:0,held:0,returned:0,adjusted:0,reward:0,net:0})
watch(()=>props.revision,()=>void load())
function ledgerDescription(row:Row){
 const fee=Math.abs(Number(row.frozen_delta||row.amount||0))
 if(row.kind==='freeze')return `${row.related_order?.product_name||(row.business==='registration'?'注册订单':'订单')}冻结 ${cash(fee)} 积分，等待办理结算`
 if(row.kind==='charge')return `${row.business==='refund'?'代退':row.business==='registration'?'注册':'订阅'}服务费结算 ${cash(Math.abs(Number(row.amount)))} 积分`
 if(row.kind==='unfreeze')return `释放冻结 ${cash(fee)} 积分，已恢复可用余额`
 return String(row.description||'—').replace(/\bR\b/g,'积分')
}
function reset(){q.value='';kind.value='';start.value='';end.value=''}
const source=(r:Row)=>r.refund_order_id?'代退订单':r.registration_order_id?'注册订单':r.related_order?'订阅订单':({invite_reward:'伙伴计划奖励',invite_reversal:'伙伴计划退回',topup:'人工充值',redeem:'兑换码入账',adjustment:'运营调整',refund:'服务退费',charge:'服务结算',freeze:'服务费冻结',unfreeze:'服务费解冻'} as Row)[r.kind]||'账户变动'
const columns=computed(()=>[{key:'kind',label:'流水类型'},{key:'source',sortKey:'business',label:'业务来源'},...props.business!=='subscription'?[]:[{key:'payment',sortKey:false as const,label:'银行卡费用'}],{key:'available',sortKey:'available_delta',label:'可用变动'},{key:'frozen',sortKey:'frozen_delta',label:'冻结变动'},{key:'balance',sortKey:'balance_after',label:'变动后余额'},{key:'related',sortKey:false as const,label:registration.value?'注册订单 / 关联记录':'关联记录',wide:true},{key:'description',sortKey:false as const,label:'说明',wide:true},{key:'time',sortKey:'created_at',label:'发生时间'}])
</script>
<template><p v-if="error" class="alert danger">{{error}}<button @click="load">重试</button></p>
 <section class="panel record-panel ledger-panel">
  <div class="records-heading"><div><h2>{{shared?'账户收支':business==='refund'?'代退资金明细':registration?'注册资金明细':'订阅资金明细'}}</h2><p>{{shared?'充值、兑换码、运营调整和伙伴奖励计入共用钱包，可用于注册、订阅与代退。':registration?'下单先冻结积分，按成功交付的账号结算；失败部分释放冻结，恢复可用余额。':'冻结转入待结算金额；确认扣费从冻结金额结算，不重复扣减可用余额。'}}</p></div><span class="pill">账户单位 · 积分</span></div>
  <div v-if="shared" class="ledger-totals"><div><span><ArrowDownLeft :size="15"/>充值 / 兑换</span><b>{{cash(totals.credit)}} <em>积分</em></b></div><div><span><ReceiptText :size="15"/>人工调整</span><b>{{signed(totals.adjusted)}} <em>积分</em></b></div><div><span><ArrowUpRight :size="15"/>伙伴奖励净额</span><b>{{signed(totals.reward)}} <em>积分</em></b></div><div><span><Outgoing :size="15"/>收支净额</span><b>{{signed(totals.net)}} <em>积分</em></b></div></div>
  <div v-else class="ledger-totals"><div><span><ArrowDownLeft :size="15"/>服务收支净额</span><b>{{signed(totals.net)}} <em>积分</em></b></div><div><span><Outgoing :size="15"/>服务扣费</span><b>{{cash(totals.spent)}} <em>积分</em></b></div><div><span><LockKeyhole :size="15"/>累计冻结</span><b>{{cash(totals.held)}} <em>积分</em></b></div><div><span><RotateCcw :size="15"/>解冻 / 退费</span><b>{{cash(totals.returned)}} <em>积分</em></b></div></div>
  <div class="records-filters">
   <label class="records-search"><span class="sr-only">{{searchLabel}}</span><Search :size="16"/><input v-model="q" type="search" :placeholder="searchLabel"/></label>
   <label><span class="sr-only">流水类型</span><select v-model="kind" aria-label="流水类型"><option value="">全部流水类型</option><option v-for="k in kinds" :key="k" :value="k">{{labels[k]||k}}</option></select></label>
   <div class="records-date-range"><label><span class="sr-only">开始日期</span><input v-model="start" type="date" aria-label="开始日期" :max="end||undefined"/></label><span>至</span><label><span class="sr-only">结束日期</span><input v-model="end" type="date" aria-label="结束日期" :min="start||undefined"/></label></div>
   <button v-if="hasFilters" class="text-btn records-reset" @click="reset">重置</button>
  </div>
  <p v-if="invalidDates" class="records-date-error" role="alert">结束日期不能早于开始日期。</p>
  <RecordsTable remote v-model:page="page" v-model:size="size" :total="data.total" :sort-by="sortBy" :sort-dir="sortDir" @sort="sort" :rows="filtered" :columns="columns" :loading="loading" :filtered="hasFilters" scope="全部资金流水；汇总随筛选更新" @reset="reset">
   <template #kind="{row}"><span class="ledger-kind" :class="'ledger-'+row.kind"><ReceiptText :size="13"/>{{labels[row.kind]||'账户变动'}}</span><small class="mono">#{{row.id}}</small></template>
   <template #source="{row}"><b>{{row.related_order?.product_name||source(row)}}</b><small v-if="row.related_order">服务费 {{cash(row.related_order.fee)}} 积分</small><small v-if="row.related_order" :class="{'positive':Number(row.related_order.price_snapshot?.standard)>row.related_order.fee}">{{discountText(row.related_order)}}</small><small v-else>账户资金</small></template>
   <template #payment="{row}"><b class="record-money">{{upstreamAmount(row.payment).value}}</b><small>{{row.related_order?upstreamAmount(row.payment).label:'不涉及上游付款'}}</small><small v-if="row.payment?.last4" class="mono">自备卡 · 尾号 {{row.payment.last4}}</small></template>
   <template #available="{row}"><b class="record-money" :class="deltaTone(row.available_delta)">{{signed(row.available_delta)}}</b></template>
   <template #frozen="{row}"><b class="record-money" :class="deltaTone(row.frozen_delta)">{{signed(row.frozen_delta)}}</b></template>
   <template #balance="{row}"><b class="record-money">{{cash(Number(row.balance_after)-Number(row.frozen_after))}} 积分</b><small>冻结 {{cash(row.frozen_after)}} 积分</small></template>
   <template #related="{row}"><a v-if="row.refund_order_id" :href="'#/orders?business=refund&order_id='+row.refund_order_id" class="text-btn">{{row.refund_order_id}}</a><template v-else-if="row.related_order"><button class="text-btn record-order-link mono" @click="$emit('order',row.related_order)">{{row.related_order.id}}<ArrowUpRight :size="13"/></button><small>{{row.related_order.email}}</small></template><a v-else-if="row.registration_order_id" :href="'/?business=registration#/orders'" class="mono">{{row.registration_order_id}}</a><span v-else class="muted">{{source(row)}}</span></template>
   <template #description="{row}"><p class="record-description">{{ledgerDescription(row)}}</p></template>
   <template #time="{row}"><time>{{date(row.created_at)}}</time></template>
  </RecordsTable>
 </section>
</template>
