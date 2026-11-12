<script setup lang="ts">
import {computed,onUnmounted,ref,watch} from 'vue'
import {ArrowDownLeft,ArrowUpRight,ChevronLeft,ChevronRight,Download,RefreshCw,Search,Wallet,LockKeyhole,ReceiptText,Users,Info} from 'lucide-vue-next'
import {api,cash,date,notify,type Row} from '../lib'
import {rememberList,toggleSort,listOwner} from '../listState'
import {financeToday,moveFinancePeriod,exportFinance} from '../financeReport'
import DataTable from './DataTable.vue'
import ListPager from './ListPager.vue'
import Modal from './Modal.vue'
import FinanceAccountExclusions from './FinanceAccountExclusions.vue'
import FinanceLedger from './FinanceLedger.vue'
import AutoRefresh from './AutoRefresh.vue'
import RegistrationPanel from './RegistrationPanel.vue'
const props=defineProps<{user:Row;pub:Row}>()
const emit=defineEmits<{refresh:[];expired:[];navigate:[path:string]}>()
const period=ref('day'),anchor=ref(financeToday()),start=ref(financeToday()),end=ref(financeToday())
const includeAdmin=ref(false),activeOnly=ref(false),status=ref(''),q=ref(''),search=ref(''),tab=ref('customers')
const page=ref(1),size=ref(10),sortBy=ref('used'),sortDir=ref('desc')
const data=ref<Row>(),loading=ref(false),error=ref(''),exporting=ref(false),selected=ref<Row>(),costs=ref(false),revision=ref(0)
const excluded=ref<Row[]>([]),storageError=ref('')
const columns=[{key:'email',label:'客户'},{key:'inflow',label:'期间入账 / 扣减'},{key:'frozen',label:'冻结 / 释放'},{key:'used',label:'期间净消费'},{key:'registration',label:'注册消费'},{key:'subscription',label:'订阅消费'},{key:'refund',label:'代退消费'},{key:'available',label:'当前可用余额'},{key:'applications',label:'期间订单 / 当前进展'},{key:'action',label:'明细',sortKey:false as const}]
rememberList('finance-reports',{period,includeAdmin,activeOnly,status,q,search,page,size,sortBy,sortDir},columns.map(c=>c.key))
if(!['day','week','month','year','custom'].includes(period.value))period.value='day'
rememberList('finance-custom-dates',{start,end})
const exclusionKey=`starbridge:finance-exclusions:v1:${listOwner()||props.user.id}`
try{const saved=JSON.parse(localStorage.getItem(exclusionKey)||'[]');if(Array.isArray(saved))excluded.value=saved.filter(x=>x&&typeof x.id==='string'&&x.id.length<=100&&typeof x.email==='string'&&typeof x.name==='string').slice(0,200).map(({id,email,name})=>({id,email,name}))}catch{}
watch(excluded,()=>{try{localStorage.setItem(exclusionKey,JSON.stringify(excluded.value));storageError.value=''}catch{storageError.value='浏览器未允许保存排除名单，刷新后需重新选择。'}})
const query=computed(()=>{const p=new URLSearchParams({period:period.value,date:anchor.value,start:start.value,end:end.value,include_admin:String(includeAdmin.value),active_only:String(activeOnly.value),status:status.value,q:q.value});excluded.value.forEach(x=>p.append('exclude_user_id',x.id));return p.toString()})
const summary=computed(()=>data.value?.summary||{})
const inflow=(r:Row)=>Number(r.topups||0)+Number(r.redeemed||0)+Number(r.added||0)
const signed=(n:number)=>`${n>0?'+':''}${cash(n)}`
const range=computed(()=>data.value?data.value.start===data.value.end?data.value.start:`${data.value.start} — ${data.value.end}`:'')
const periods=[{id:'day',name:'每日'},{id:'week',name:'每周'},{id:'month',name:'每月'},{id:'year',name:'每年'},{id:'custom',name:'自定义'}]
const names:Row={registration:'注册',subscription:'订阅',refund:'代退'}
const chart=computed(()=>{
 if(!data.value)return []
 const points=new Map((data.value.trend||[]).map((v:Row)=>[v.day,v]))
 const items:Row[]=[],day=new Date(data.value.start+'T00:00:00Z'),last=new Date(data.value.end+'T00:00:00Z')
 if(data.value.trend_period==='month')day.setUTCDate(1)
 while(day<=last&&items.length<366){const key=day.toISOString().slice(0,10),v=points.get(key) as Row|undefined;items.push({day:key,inflow:inflow(v||{}),used:Number(v?.used||0)});if(data.value.trend_period==='month')day.setUTCMonth(day.getUTCMonth()+1);else day.setUTCDate(day.getUTCDate()+1)}
 return items
})
const chartMax=computed(()=>Math.max(1,...chart.value.flatMap(p=>[p.inflow,Math.abs(p.used)])))
const hasNegative=computed(()=>chart.value.some(p=>p.used<0))
let controller:AbortController|undefined
async function load(){controller?.abort();const current=new AbortController();controller=current;loading.value=true;error.value=''
 try{const result=await api('/admin/finance/reports?'+query.value+'&'+new URLSearchParams({page:String(page.value),page_size:String(size.value),sort_by:sortBy.value,sort_dir:sortDir.value}),'GET',undefined,{},current.signal);if(controller===current){data.value=result;if(page.value!==result.page)page.value=result.page}}
 catch(e){if(controller===current&&(e as Error).name!=='AbortError')error.value=(e as Error).message}
 finally{if(controller===current)loading.value=false}}
function refresh(){if(loading.value)return;revision.value++;void load()}
function sort(field:string){toggleSort(field,sortBy,sortDir,page)}
function applySearch(){q.value=search.value.trim();page.value=1;refresh()}
function shift(direction:number){anchor.value=moveFinancePeriod(data.value?.start||anchor.value,period.value,direction)}
async function download(){exporting.value=true;try{const p=new URLSearchParams(query.value);p.set('sort_by',sortBy.value);p.set('sort_dir',sortDir.value);await exportFinance(p,'accounts')}catch(e){notify((e as Error).message)}finally{exporting.value=false}}
watch([query,size],()=>{page.value=1;selected.value=undefined},{flush:'sync'})
watch([query,page,size,sortBy,sortDir],load,{immediate:true})
onUnmounted(()=>controller?.abort())
</script>
<template><div class="finance-report" :aria-busy="loading">
 <header class="page-heading"><div><span class="eyebrow">FINANCE</span><h1>资金中心</h1><p>查看收支、业务结算与每位客户的资金情况。</p></div><div class="finance-actions"><button class="btn secondary" :disabled="exporting||loading" @click="download"><Download :size="15"/>{{exporting?'导出中…':'导出客户报表'}}</button><AutoRefresh storage-key="admin-finance" :busy="loading" :paused="!!selected||costs||exporting" @refresh="refresh"/></div></header>
 <section class="panel finance-controls"><div class="finance-period-row"><div class="finance-periods" role="group" aria-label="财务统计周期"><button v-for="item in periods" :key="item.id" :aria-pressed="period===item.id" :class="{active:period===item.id}" @click="period=item.id">{{item.name}}</button></div>
  <div v-if="period!=='custom'" class="finance-date-nav"><button class="icon-btn" aria-label="上一期" :disabled="loading" @click="shift(-1)"><ChevronLeft :size="17"/></button><input v-model="anchor" type="date" min="1900-01-01" max="9998-12-31" aria-label="财务统计日期"/><button class="icon-btn" aria-label="下一期" :disabled="loading" @click="shift(1)"><ChevronRight :size="17"/></button><button class="text-btn" @click="anchor=financeToday();refresh()">本期</button></div>
  <div v-else class="finance-custom"><input v-model="start" type="date" min="1900-01-01" :max="end" aria-label="财务开始日期"/><span>至</span><input v-model="end" type="date" :min="start" max="9998-12-31" aria-label="财务结束日期"/></div>
  <label class="finance-check"><input v-model="includeAdmin" type="checkbox"/>包含管理员</label><FinanceAccountExclusions v-model="excluded" :include-admin="includeAdmin"/></div>
  <form class="finance-filter-row" @submit.prevent="applySearch"><label class="finance-search"><Search :size="16"/><input v-model="search" placeholder="搜索客户称呼、邮箱或账户 ID" aria-label="搜索财务客户" maxlength="200"/></label><select v-model="status" aria-label="财务客户状态"><option value="">全部账户状态</option><option value="active">正常账户</option><option value="disabled">停用账户</option><option value="deleted">已删除账户</option></select><label class="finance-check"><input v-model="activeOnly" type="checkbox"/>只看本期有流水或订单的客户</label><button class="btn secondary">查询</button><button v-if="q||status||activeOnly||excluded.length" class="text-btn" type="button" @click="search='';q='';status='';activeOnly=false;excluded=[];refresh()">清除筛选</button></form>
 </section>
 <p v-if="storageError" class="alert" role="status">{{storageError}}</p><div v-if="error" class="alert danger" role="alert">{{data?'刷新失败，以下为上次查询结果。':''}}{{error}}<button @click="load">重试</button></div>
 <div class="finance-caption"><div><b>{{range||'正在读取报表…'}}</b><span>北京时间 · {{data?.total??'—'}} 位客户{{includeAdmin?'':' · 不含管理员'}}<template v-if="excluded.length"> · 排除 {{excluded.length}} 个账号</template></span></div><small v-if="data">更新于 {{date(data.generated_at)}}</small></div>
 <section class="finance-metrics" aria-label="财务汇总">
  <article class="panel"><div class="metric-label"><span>本期入账</span><ArrowDownLeft :size="18"/></div><strong>{{data?cash(inflow(summary)):'—'}}<small>积分</small></strong><p>充值 / 兑换 {{cash(Number(summary.topups||0)+Number(summary.redeemed||0))}}<br/>人工加款 {{cash(summary.added)}} · 扣减 {{cash(summary.debited)}}</p></article>
  <article class="panel metric-featured"><div class="metric-label"><span>本期净消费</span><ReceiptText :size="18"/></div><strong>{{data?cash(summary.used):'—'}}<small>积分</small></strong><p>扣费 {{cash(summary.charged)}} − 退费 {{cash(summary.returned)}}<br/>结算 {{summary.settled_count||0}} 笔订单</p></article>
  <article class="panel"><div class="metric-label"><span>当前冻结</span><LockKeyhole :size="17"/></div><strong>{{data?cash(summary.frozen):'—'}}<small>积分</small></strong><p>本期新增 {{cash(summary.freeze_added)}}<br/>未结算释放 {{cash(summary.released)}}</p></article>
  <article class="panel"><div class="metric-label"><span>当前可用余额</span><Wallet :size="18"/></div><strong>{{data?cash(summary.available):'—'}}<small>积分</small></strong><p>账户总余额 {{cash(summary.balance)}}<br/>本期伙伴奖励净额 {{cash(summary.rewarded)}}</p></article>
 </section>
 <nav class="finance-view-tabs" aria-label="资金中心页面"><button v-for="item in [{id:'customers',name:'客户报表'},{id:'overview',name:'资金总览'},{id:'business',name:'业务明细'}]" :key="item.id" :aria-current="tab===item.id?'page':undefined" :class="{active:tab===item.id}" @click="tab=item.id">{{item.name}}</button><details class="finance-definition"><summary><Info :size="14"/>统计说明</summary><div><p>收支按账本发生时间统计。冻结不算消费，释放不算退费。</p><p>余额与冻结为当前值；订单按本期提交统计，进展为当前状态。订阅付款成功计为完成。</p><p>汇总与导出覆盖全部筛选结果。净消费未扣除上游成本，不代表利润。</p></div></details></nav>
 <template v-if="tab==='overview'">
  <section class="finance-business-cards" aria-label="三业务财务"><article v-for="b in data?.businesses||[]" :key="b.business" class="panel"><header><span class="finance-business-dot" :class="b.business"/><h2>{{names[b.business]}}业务</h2><a :href="`#/admin/orders?business=${b.business}`" :aria-label="`查看${names[b.business]}订单`"><ArrowUpRight :size="17"/></a></header><div class="finance-business-total"><span>本期净消费</span><b>{{cash(b.used)}}<small>积分</small></b></div><div class="finance-business-meta"><span>扣费 {{cash(b.charged)}}</span><span>退费 {{cash(b.returned)}}</span><span>结算 {{b.settled_count}} 笔</span></div><div class="finance-order-counts"><div><b>{{b.applications}}</b><span>提交订单</span></div><div><b>{{b.succeeded}}</b><span>已完成</span></div><div><b>{{Number(b.queued)+Number(b.processing)}}</b><span>待办 / 办理中</span></div><div><b>{{b.attention}}</b><span>需跟进</span></div></div><footer><span v-if="b.business==='registration'">交付 {{b.delivered}} 个 · 部分交付 {{b.partial}} 笔</span><span v-else>失败 {{b.failed}} 笔 · 撤回 {{b.cancelled}} 笔</span><button v-if="b.business==='registration'" class="text-btn" @click="costs=true">成本明细</button></footer></article></section>
  <section class="panel finance-trend"><header><div><h2>入账与消费趋势</h2><p>{{data?.trend_period==='month'?'按月汇总':'按日汇总'}} · {{range}}</p></div><div class="finance-legend"><span><i/>入账</span><span><i/>净消费</span></div></header><div class="finance-chart" :class="{signed:hasNegative}" role="group" aria-label="财务收支趋势"><div v-for="(p,i) in chart" :key="p.day" tabindex="0" class="finance-chart-point" :aria-label="`${p.day} 入账 ${cash(p.inflow)}，净消费 ${cash(p.used)} 积分`"><div class="finance-chart-bars"><i :style="{height:Math.max(p.inflow?1:0,p.inflow/chartMax*100)/(hasNegative?2:1)+'%'}"/><i :class="{negative:p.used<0}" :style="{height:Math.max(p.used?1:0,Math.abs(p.used)/chartMax*100)/(hasNegative?2:1)+'%'}"/></div><small v-if="chart.length<15||i%Math.ceil(chart.length/9)===0">{{data?.trend_period==='month'?p.day.slice(0,7):p.day.slice(5)}}</small><div class="finance-chart-tip">{{p.day}}<br/>入账 {{cash(p.inflow)}}<br/>净消费 {{cash(p.used)}}</div></div></div><p v-if="data&&!chart.some(x=>x.inflow||x.used)" class="finance-no-trend">本期暂无入账或消费</p></section>
  <section class="panel finance-overview-foot"><div><Users :size="20"/><div><h2>本期提交 {{summary.applications||0}} 笔订单</h2><p>完成 {{summary.succeeded||0}} · 部分交付 {{summary.partial||0}} · 排队 {{summary.queued||0}} · 办理中 {{summary.processing||0}} · 需跟进 {{summary.attention||0}}</p></div></div><button class="btn secondary" @click="tab='customers'">按客户对账<ArrowUpRight :size="15"/></button></section>
 </template>
 <FinanceLedger v-else-if="tab==='business'" :query="query" :revision="revision"/>
 <section v-else class="panel finance-customers"><header class="records-heading"><div><h2>客户报表</h2><p>期间收支、三类业务消费及当前余额，按人核对。</p></div><span class="pill">{{data?.total||0}} 位客户</span></header>
  <DataTable paged hide-toolbar hide-pagination :rows="data?.items||[]" :columns="columns" :sort-by="sortBy" :sort-dir="sortDir" @sort="sort">
   <template #email="{row}"><b>{{row.name||'未填写称呼'}}</b><small>{{row.email}}</small><span v-if="row.deleted_at||row.status!=='active'||row.role==='admin'" class="customer-state">{{row.deleted_at?'已删除':row.status!=='active'?'已停用':'管理员'}}</span></template>
   <template #inflow="{row}"><b class="positive">+{{cash(inflow(row))}}</b><small>充值 / 兑换 {{cash(Number(row.topups)+Number(row.redeemed))}}</small><small>人工加款 {{cash(row.added)}} · 扣减 {{cash(row.debited)}}</small></template>
   <template #frozen="{row}"><b>{{cash(row.frozen)}}<em>当前</em></b><small>本期冻结 {{cash(row.freeze_added)}}</small><small>本期释放 {{cash(row.released)}}</small></template>
   <template #used="{row}"><b class="finance-used">{{cash(row.used)}}</b><small>结算 {{row.settled_count}} 笔 · 退费 {{cash(row.returned)}}</small><small>平均扣费 {{row.settled_count?cash(row.charged/row.settled_count):'—'}} / 笔</small></template>
   <template v-for="b in ['registration','subscription','refund']" #[b]="{row}"><b>{{cash(row[b])}}</b></template>
   <template #available="{row}"><b>{{cash(row.available)}}</b><small>总余额 {{cash(row.balance)}}</small><small>伙伴奖励 {{signed(row.rewarded)}}</small></template>
   <template #applications="{row}"><b>{{row.applications}} 笔</b><div class="finance-progress-grid"><span>完成 <b>{{row.succeeded}}</b></span><span>部分 <b>{{row.partial}}</b></span><span>排队 <b>{{row.queued}}</b></span><span>办理中 <b>{{row.processing}}</b></span><span>跟进 <b>{{row.attention}}</b></span><span>失败 <b>{{row.failed}}</b></span><span>撤回 <b>{{row.cancelled}}</b></span></div></template>
   <template #action="{row}"><button class="text-btn" @click="selected=row">查看流水</button></template>
   <template #empty><strong>{{loading?'正在汇总客户资金…':'没有符合条件的客户'}}</strong><p>可调整日期或账户筛选。</p></template>
  </DataTable><ListPager v-model:page="page" v-model:size="size" :total="data?.total||0" :loading="loading"/>
 </section>
 <p class="finance-bottom-note">金额单位：积分。账户收支共用一个钱包，注册 → 订阅 → 代退分别统计实际消费。</p>
 <Modal v-if="selected" title="客户资金明细" wide @close="selected=undefined"><div class="finance-customer-balance"><span>当前可用 <b>{{cash(selected.available)}}</b></span><span>当前冻结 <b>{{cash(selected.frozen)}}</b></span><span>本期净消费 <b>{{cash(selected.used)}}</b></span></div><FinanceLedger :key="selected.id" :query="query" :customer="selected" :revision="revision"/></Modal>
 <Modal v-if="costs" title="注册成本明细" wide @close="costs=false"><p class="muted small">按 {{range}} 内提交的注册订单统计，含后续执行成本。此成本报表独立筛选账户。</p><RegistrationPanel route="/admin/finance" :enabled="pub.registration_enabled===true" :user="user" :pub="pub" :finance-filter="{start:data?.start,end:data?.end,period:'day',include_admin:String(includeAdmin)}" @refresh="emit('refresh')" @expired="emit('expired')" @navigate="emit('navigate',$event)"/></Modal>
</div></template>
<style scoped src="../finance-report.css"></style>
