<script setup lang="ts">
import {computed,onUnmounted,ref,watch} from 'vue'
import {Download,Search} from 'lucide-vue-next'
import {api,cash,date,labels,type Row,notify} from '../lib'
import {rememberList,toggleSort} from '../listState'
import DataTable from './DataTable.vue'
import ListPager from './ListPager.vue'
import {exportFinance} from '../financeReport'
const props=defineProps<{query:string;customer?:Row;revision:number}>()
const page=ref(1),size=ref(10),sortBy=ref('created_at'),sortDir=ref('desc'),business=ref(''),kind=ref(''),search=ref(''),draftSearch=ref('')
const data=ref<Row>(),loading=ref(false),error=ref(''),exporting=ref(false)
const businesses:Row={shared:'账户收支',registration:'注册',subscription:'订阅',refund:'代退'}
const kinds=['topup','redeem','adjustment','invite_reward','invite_reversal','freeze','unfreeze','charge','refund']
const columns=[{key:'created_at',label:'时间'},{key:'email',label:'客户'},{key:'kind',label:'类型 / 业务'},{key:'amount',label:'余额变动'},{key:'available_delta',label:'可用变动'},{key:'frozen_delta',label:'冻结变动'},{key:'balance_after',label:'变动后余额'},{key:'record',label:'关联 / 说明',sortKey:false as const}]
rememberList(props.customer?'finance-customer-ledger':'finance-business-ledger',{page,size,sortBy,sortDir,business,kind,search,draftSearch},columns.map(c=>c.key))
if(props.customer)page.value=1
const params=computed(()=>{const p=new URLSearchParams(props.query);Object.entries({page:String(page.value),page_size:String(size.value),sort_by:sortBy.value,sort_dir:sortDir.value,business:business.value,kind:kind.value,search:search.value,user_id:props.customer?.id||''}).forEach(([k,v])=>p.set(k,v));return p})
const signed=(n:number)=>`${n>0?'+':''}${cash(n)}`
let controller:AbortController|undefined
async function load(){controller?.abort();const current=new AbortController();controller=current;loading.value=true;error.value=''
 try{const result=await api('/admin/finance/reports/ledger?'+params.value,'GET',undefined,{},current.signal);if(controller===current){data.value=result;if(page.value!==result.page)page.value=result.page}}
 catch(e){if(controller===current&&(e as Error).name!=='AbortError')error.value=(e as Error).message}
 finally{if(controller===current)loading.value=false}}
function sort(field:string){toggleSort(field,sortBy,sortDir,page)}
function apply(){search.value=draftSearch.value.trim();page.value=1;void load()}
async function download(){exporting.value=true;try{await exportFinance(params.value,'ledger')}catch(e){notify((e as Error).message)}finally{exporting.value=false}}
watch([business,kind,size,()=>props.query],()=>page.value=1,{flush:'sync'})
watch([page,size,sortBy,sortDir,business,kind,()=>props.query],load,{immediate:true})
watch(()=>props.revision,()=>{if(!loading.value)void load()})
onUnmounted(()=>controller?.abort())
</script>
<template><section class="panel finance-ledger" :aria-busy="loading">
 <header class="records-heading"><div><h2>{{customer?'账户流水':'业务明细'}}</h2><p>{{customer?`${customer.name||customer.email} · ${customer.email}`:'每一笔入账、冻结、释放、扣费和退费均可核对'}}</p></div><button class="btn secondary" :disabled="exporting||loading" @click="download"><Download :size="15"/>{{exporting?'导出中…':'导出流水'}}</button></header>
 <form class="records-filters" @submit.prevent="apply"><label class="records-search"><Search :size="15"/><input v-model="draftSearch" placeholder="订单编号 / 流水编号 / 说明" aria-label="搜索财务流水" maxlength="200"/></label><select v-model="business" aria-label="财务流水业务"><option value="">全部业务</option><option v-for="b in ['registration','subscription','refund','shared']" :key="b" :value="b">{{businesses[b]}}</option></select><select v-model="kind" aria-label="财务流水类型"><option value="">全部类型</option><option v-for="k in kinds" :key="k" :value="k">{{labels[k]}}</option></select><button class="btn secondary">查询</button></form>
 <div v-if="error" class="alert danger" role="alert">{{error}}<button @click="load">重试</button></div>
 <div v-if="data" class="ledger-summary"><span>共 <b>{{data.total}}</b> 条流水</span><span>余额净变动 <b>{{signed(data.summary.net)}}</b></span><span>可用净变动 <b>{{signed(data.summary.available)}}</b></span><span>冻结净变动 <b>{{signed(data.summary.frozen)}}</b></span><small>积分</small></div>
 <DataTable paged hide-toolbar hide-pagination :rows="data?.items||[]" :columns="columns" :sort-by="sortBy" :sort-dir="sortDir" @sort="sort">
  <template #created_at="{row}"><time>{{date(row.created_at)}}</time><small>#{{row.id}}</small></template><template #email="{row}"><b>{{row.name||'未填写称呼'}}</b><small>{{row.email}}</small></template>
  <template #kind="{row}"><span class="finance-ledger-kind">{{labels[row.kind]||row.kind}}</span><small>{{businesses[row.business]}}</small></template>
  <template v-for="field in ['amount','available_delta','frozen_delta']" #[field]="{row}"><b :class="{'money-positive':row[field]>0,'money-negative':row[field]<0}">{{signed(row[field])}}</b></template>
  <template #balance_after="{row}"><b>{{cash(row.balance_after)}}</b><small>可用 {{cash(row.balance_after-row.frozen_after)}} · 冻结 {{cash(row.frozen_after)}}</small></template>
  <template #record="{row}"><span class="ledger-note">{{row.description||'—'}}</span><a v-if="row.order_id" class="ledger-order" :href="`#/admin/orders?business=${row.business}&order_id=${encodeURIComponent(row.order_id)}`">{{row.order_id}}</a></template>
  <template #empty><strong>{{loading?'正在读取流水…':'没有符合条件的流水'}}</strong><p>可调整日期、业务或类型筛选。</p></template>
 </DataTable><ListPager v-model:page="page" v-model:size="size" :total="data?.total||0" :loading="loading"/>
</section></template>
<style scoped>
.finance-ledger{overflow:hidden}.finance-ledger :deep(table){min-width:1120px}.finance-ledger :deep(td){font-variant-numeric:tabular-nums;vertical-align:top;padding:17px 18px;white-space:nowrap}.finance-ledger :deep(td small){display:block;color:var(--muted);font-size:11px;margin-top:6px;font-weight:400}.finance-ledger :deep(td:nth-child(2)){max-width:230px;overflow-wrap:anywhere;white-space:normal}.finance-ledger :deep(td:nth-child(8)){width:260px;white-space:normal;min-width:190px}.ledger-note,.ledger-order{display:block;overflow-wrap:anywhere;font-size:12px}.ledger-order{margin-top:7px;font-family:monospace;color:var(--green)}.finance-ledger :deep(.table-scroll){max-width:100%;overflow:auto}.ledger-summary{display:flex;flex-wrap:wrap;gap:10px 28px;padding:13px 24px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);background:#f7faf8;font-size:12px;color:var(--muted)}.ledger-summary b{margin-left:6px;color:var(--text)}.money-positive{color:#287450}.money-negative{color:#955137}.finance-ledger :deep(.records-pagination){padding:18px 24px}.finance-ledger-kind{font-size:12px}.finance-ledger .records-heading p{overflow-wrap:anywhere}
</style>
