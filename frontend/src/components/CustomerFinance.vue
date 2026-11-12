<script setup lang="ts">
import {computed,onUnmounted,ref,watch} from 'vue'
import {Search,ChevronLeft,ChevronRight} from 'lucide-vue-next'
import {api,cash,date,labels,type Row} from '../lib'
import DataTable from './DataTable.vue'
import ListPager from './ListPager.vue'
import {rememberList,toggleSort} from '../listState'
import Modal from './Modal.vue'
const props=defineProps<{filter:Row;revision:number}>()
const data=ref<Row>({items:[],total:0}),q=ref(''),search=ref(''),page=ref(1),size=ref(10),loading=ref(false),error=ref('')
const selected=ref(''),detail=ref<Row>(),detailPage=ref(1),detailError=ref(''),detailLoading=ref(false)
const sortBy=ref('created_at'),sortDir=ref('desc'),detailSize=ref(10),detailSortBy=ref('created_at'),detailSortDir=ref('desc'),detailSearch=ref(''),detailKind=ref(''),detailBusiness=ref('')
rememberList('admin-customer-finance',{page,size,sortBy,sortDir,q,search})
rememberList('admin-customer-ledger',{size:detailSize,sortBy:detailSortBy,sortDir:detailSortDir,q:detailSearch,kind:detailKind,business:detailBusiness})
function sort(field:string){toggleSort(field,sortBy,sortDir,page);void load()}
function sortDetail(field:string){toggleSort(field,detailSortBy,detailSortDir,detailPage);void readDetail()}
const pages=computed(()=>Math.max(1,Math.ceil(data.value.total/size.value)))
let controller:AbortController|undefined,detailController:AbortController|undefined
const columns=[{key:'customer',sortKey:'email',label:'客户'},{key:'credited',label:'充值 / 兑换'},{key:'adjusted',label:'人工调整'},{key:'rewarded',label:'伙伴奖励净额'},{key:'registration',label:'注册结算'},{key:'subscription',label:'订阅结算'},{key:'refund',label:'代退结算'},{key:'settled',label:'合计净结算'},{key:'available',label:'当前可用'},{key:'frozen',label:'当前冻结'},{key:'action',label:'流水'}]
const moneyColumns=['credited','adjusted','rewarded','registration','subscription','refund','frozen']
const businesses:Row={shared:'账户收支',registration:'注册',subscription:'订阅',refund:'代退'}
const signed=(n:number)=>`${n>0?'+':''}${cash(n)}`
async function load(){
 controller?.abort();const request=new AbortController();controller=request;loading.value=true;error.value=''
 try{const result=await api('/admin/finance/customers?'+new URLSearchParams({...props.filter,q:search.value,page:String(page.value),page_size:String(size.value),sort_by:sortBy.value,sort_dir:sortDir.value}),'GET',undefined,{},request.signal);if(controller===request){data.value=result;page.value=result.page}}
 catch(e){if(controller===request&&(e as Error).name!=='AbortError')error.value=(e as Error).message}
 finally{if(controller===request)loading.value=false}
}
function apply(){search.value=q.value.trim();page.value=1;void load()}
function close(){selected.value='';detailController?.abort();detail.value=undefined}
async function readDetail(){
 detailController?.abort();const request=new AbortController();detailController=request;detailLoading.value=true;detailError.value=''
 try{const result=await api('/admin/finance/customers/'+encodeURIComponent(selected.value)+'?'+new URLSearchParams({...props.filter,page:String(detailPage.value),page_size:String(detailSize.value),sort_by:detailSortBy.value,sort_dir:detailSortDir.value,q:detailSearch.value,kind:detailKind.value,business:detailBusiness.value}),'GET',undefined,{},request.signal);if(detailController===request){detail.value=result;detailPage.value=result.page}}
 catch(e){if(detailController===request&&(e as Error).name!=='AbortError')detailError.value=(e as Error).message}
 finally{if(detailController===request)detailLoading.value=false}
}
function open(id:string){close();selected.value=id;detailPage.value=1;void readDetail()}
watch(()=>props.filter,(_,old)=>{if(old)page.value=1;close();void load()},{immediate:true})
watch(()=>props.revision,()=>{void load()})
onUnmounted(()=>{controller?.abort();detailController?.abort()})
</script>
<template>
 <section class="panel customer-finance" :aria-busy="loading">
  <div class="records-heading"><div><h2>客户资金明细</h2><p>收支与结算按所选日期统计，余额和冻结显示当前金额。单位：积分。</p></div><span class="pill">{{data.total}} 位客户</span></div>
  <form class="records-filters" @submit.prevent="apply"><label class="records-search"><Search :size="16"/><input v-model="q" aria-label="搜索资金账户" placeholder="邮箱 / 名称 / 账户 ID" maxlength="200"/></label><button class="btn secondary" :disabled="loading">查询客户</button><button v-if="search" class="text-btn" type="button" @click="q='';apply()">重置</button></form>
  <p v-if="error" class="alert danger" role="alert">{{error}}<button @click="load">重试</button></p>
  <DataTable paged :sort-by="sortBy" :sort-dir="sortDir" @sort="sort" :rows="data.items" :columns="columns" :page-size="size" hide-toolbar hide-pagination>
   <template #customer="{row}"><b>{{row.name||'未填写名称'}}</b><small>{{row.email}}</small><small v-if="row.deleted_at">已删除账户</small></template>
   <template v-for="field in moneyColumns" #[field]="{row}">{{cash(row[field])}}</template>
   <template #settled="{row}"><b>{{cash(Number(row.charged)-Number(row.returned))}}</b><small>含退费 {{cash(row.returned)}}</small></template>
   <template #available="{row}"><b>{{cash(Number(row.balance)-Number(row.frozen))}}</b></template>
   <template #action="{row}"><button class="text-btn" @click="open(row.id)">查看流水</button></template>
  </DataTable>
  <ListPager v-model:page="page" v-model:size="size" :total="data.total" :loading="loading" @change="load"/>
 </section>
 <Modal v-if="selected" title="客户资金流水" wide @close="close">
  <p v-if="detailError" class="alert danger" role="alert">{{detailError}}<button @click="readDetail">重试</button></p>
  <template v-if="detail"><div class="detail-title"><div><h3>{{detail.user.name||detail.user.email}}</h3><p>{{detail.user.email}}</p></div><span>{{detail.start}} 至 {{detail.end}}</span></div>
   <div class="customer-funds"><div><span>当前可用</span><b>{{cash(Number(detail.user.balance)-Number(detail.user.frozen))}}</b></div><div><span>当前冻结</span><b>{{cash(detail.user.frozen)}}</b></div><div><span>本期充值 / 兑换</span><b>{{cash(detail.totals.credited)}}</b></div><div><span>本期净结算</span><b>{{cash(Number(detail.totals.charged)-Number(detail.totals.returned))}}</b></div></div>
   <form class="records-filters" @submit.prevent="detailPage=1;readDetail()"><input v-model="detailSearch" placeholder="搜索订单 / 流水说明" aria-label="搜索客户流水"/><select v-model="detailBusiness" aria-label="流水业务"><option value="">全部业务</option><option v-for="(n,k) in businesses" :value="k">{{n}}</option></select><select v-model="detailKind" aria-label="流水类型"><option value="">全部类型</option><option v-for="k in ['topup','redeem','adjustment','invite_reward','invite_reversal','freeze','unfreeze','charge','refund']" :value="k">{{labels[k]||k}}</option></select><button class="btn secondary">查询</button></form><DataTable paged :sort-by="detailSortBy" :sort-dir="detailSortDir" @sort="sortDetail" class="customer-ledger" :rows="detail.items" :page-size="25" hide-toolbar hide-pagination :columns="[{key:'type',sortKey:'kind',label:'流水类型 / 业务'},{key:'available_delta',label:'可用变动'},{key:'frozen_delta',label:'冻结变动'},{key:'balance',sortKey:'balance_after',label:'变动后余额'},{key:'record',sortKey:false as const,label:'关联 / 说明'},{key:'time',sortKey:'created_at',label:'时间'}]">
    <template #type="{row}"><b>{{labels[row.kind]||row.kind}}</b><small>{{businesses[row.business]}}</small></template><template #available_delta="{row}">{{signed(row.available_delta)}}</template><template #frozen_delta="{row}">{{signed(row.frozen_delta)}}</template><template #balance="{row}">{{cash(Number(row.balance_after)-Number(row.frozen_after))}}<small>冻结 {{cash(row.frozen_after)}}</small></template><template #record="{row}"><small v-if="row.order_id" class="mono">{{row.order_id}}</small><span>{{row.description}}</span></template><template #time="{row}">{{date(row.created_at)}}</template>
   </DataTable>
   <ListPager v-model:page="detailPage" v-model:size="detailSize" :total="detail.total" :loading="detailLoading" @change="readDetail"/>
  </template><p v-else-if="!detailError" role="status">正在读取资金流水…</p>
 </Modal>
</template>
<style scoped>
.customer-finance :deep(td){white-space:nowrap}.customer-finance :deep(td small),.customer-ledger :deep(td small){display:block;margin-top:6px;color:var(--muted);font-size:11px}.customer-finance :deep(td:first-child){min-width:190px}.customer-finance .pagination label{display:flex;gap:8px;align-items:center}.pagination select{width:auto}.customer-funds{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:20px 0}.customer-funds>div{padding:16px;background:#f2f7f5;border-radius:10px}.customer-funds span{font-size:11px;color:var(--muted)}.customer-funds b{display:block;margin-top:10px;font-size:20px}.customer-ledger{min-width:0}.customer-ledger :deep(table){min-width:800px}.customer-ledger :deep(td){min-width:100px}.customer-ledger :deep(td:nth-child(5)){max-width:260px;overflow-wrap:anywhere}.customer-ledger :deep(.table-scroll){max-width:100%;overflow:auto}
@media(max-width:700px){.customer-funds{grid-template-columns:repeat(2,minmax(0,1fr))}.detail-title{align-items:flex-start;gap:12px;flex-direction:column}.customer-finance :deep(.table-scroll){overflow:auto}.customer-finance :deep(table){min-width:1100px}}
</style>
