<script setup lang="ts">
import {computed,defineAsyncComponent,onMounted,onUnmounted,ref} from 'vue'
import {Activity,ArrowUpRight,CheckCheck,ChevronLeft,ChevronRight,Clock3,Filter,RefreshCw,Search,TriangleAlert} from 'lucide-vue-next'
import {api,cash,date,orderLabel,orderTone,type Row} from '../lib'
import {rangePresets,presetRange} from '../lib/report-period'
import DataTable from './DataTable.vue'
import Modal from './Modal.vue'
import SubscriptionRenewal from './SubscriptionRenewal.vue'
import OrderDuration from './OrderDuration.vue'
const AdminActivityRecord=defineAsyncComponent(()=>import('./AdminActivityRecord.vue'))
const props=defineProps<{admin?:boolean;user?:Row;pub?:Row}>()
const record=ref<Row|null>(null)
function openRecord(row:Row){if(props.admin)record.value=row;else detail.value=row}
const data=ref<Row>({items:[],summary:{},businesses:[],total:0,show_fees:false}),loading=ref(false),error=ref(''),detail=ref<Row|null>(null)
const period=ref('today'),initialRange=presetRange('today')
function selectPeriod(id:string){period.value=id;if(id!=='custom'){const range=presetRange(id);start.value=range.start;end.value=range.end;apply()}}
const search=ref(''),business=ref(''),state=ref(''),start=ref(initialRange.start),end=ref(initialRange.end),includeAdmin=ref(true),page=ref(1),size=ref(25)
const applied=ref<Row>({}),names:Row={registration:'注册',subscription:'订阅',refund:'代退'}
const states:Row={active:'进行中',completed:'已完成',partial:'部分完成',attention:'需核实',failed:'未完成',cancelled:'已取消'}
const businesses=computed(()=>['registration','subscription','refund'].map(id=>({id,...(data.value.businesses.find((r:Row)=>r.business===id)||{total:0,active:0,settled:0})})))
const pages=computed(()=>Math.max(1,Math.ceil(data.value.total/size.value)))
const columns=computed(()=>[{key:'record',label:'办理项目'},...(props.admin?[{key:'owner',label:'客户'}]:[]),{key:'progress',label:'当前状态'},...(data.value.show_fees?[{key:'fees',label:'服务费'}]:[]),{key:'duration',label:'总耗时'},{key:'time',label:'办理时间'},{key:'action',label:'详情'}])
let controller:AbortController|undefined,timer:ReturnType<typeof setInterval>|undefined
function status(row:Row){if(row.workflow==='renewal')return ({disabled:'已关闭自动续费',no_subscription:'无可关闭订阅',checking:'核实中'} as Row)[row.status]||states[row.state];return row.business==='subscription'?orderLabel(row):row.state==='active'?({queued:'排队中',running:'办理中',processing:'办理中',verifying:'核实中'} as Row)[row.status]||states.active:states[row.state]}
function tone(row:Row){return row.business==='subscription'&&row.workflow!=='renewal'?orderTone(row):({completed:'succeeded',partial:'review',attention:'review',failed:'failed',cancelled:'cancelled',active:'processing'} as Row)[row.state]}
async function load(quiet=false){
 controller?.abort();const request=new AbortController();controller=request;if(!quiet)loading.value=true;error.value=''
 try{const query=new URLSearchParams({...applied.value,page:String(page.value),page_size:String(size.value)});const result=await api((props.admin?'/admin':'')+'/activity?'+query,'GET',undefined,{},request.signal);if(controller===request){data.value=result;if(page.value>pages.value){page.value=pages.value;void load()}}}
 catch(e){if((e as Error).name!=='AbortError'&&controller===request)error.value=(e as Error).message}
 finally{if(controller===request)loading.value=false}
}
function apply(){applied.value={q:search.value,business:business.value,state:state.value,start:start.value,end:end.value,...(props.admin?{include_admin:String(includeAdmin.value)}:{})};page.value=1;void load()}
function reset(){period.value='today';const range=presetRange('today');search.value='';business.value='';state.value='';start.value=range.start;end.value=range.end;includeAdmin.value=true;apply()}
function selectState(value:string){state.value=value;apply()}
onMounted(()=>{apply();timer=setInterval(()=>{if(document.visibilityState==='visible'&&!loading.value)void load(true)},30000)})
onUnmounted(()=>{controller?.abort();clearInterval(timer)})
</script>
<template>
 <section class="activity-overview" :class="{'is-admin':admin}">
  <div class="page-heading"><div><h1>办理总览</h1><p>{{admin?'查看全站办理记录、客户进度和服务费结算。':'注册、订阅和代退的全部办理记录。'}}</p></div><button class="btn secondary" :disabled="loading" @click="load()"><RefreshCw :size="16"/>刷新</button></div>
  <div v-if="error" class="alert danger" role="alert">{{error}} <button class="text-btn" @click="load()">重试</button></div>
  <div class="activity-period" aria-label="统计日期"><button v-for="item in rangePresets" :key="item.id" :class="{active:period===item.id}" :aria-pressed="period===item.id" @click="selectPeriod(item.id)">{{item.label}}</button><span>北京时间 · 按提交日期</span></div>
  <div class="activity-stats" :aria-busy="loading">
   <button :class="{selected:!state}" @click="selectState('')"><Activity :size="19"/><span>全部办理<strong>{{data.summary.total||0}}</strong><small>当前筛选范围</small></span></button>
   <button :class="{selected:state==='active'}" @click="selectState('active')"><Clock3 :size="19"/><span>进行中<strong>{{data.summary.active||0}}</strong><small>排队、执行与后续处理中</small></span></button>
   <button :class="{selected:state==='completed'}" @click="selectState('completed')"><CheckCheck :size="19"/><span>已完成<strong>{{data.summary.completed||0}}</strong><small>办理结果已确认</small></span></button>
   <button :class="{selected:state==='attention'}" @click="selectState('attention')"><TriangleAlert :size="19"/><span>需核实<strong>{{data.summary.attention||0}}</strong><small>等待进一步确认</small></span></button>
  </div>
  <div class="activity-businesses"><button v-for="item in businesses" :key="item.id" :class="{selected:business===item.id}" @click="business=business===item.id?'':item.id;apply()"><span>{{names[item.id]}}<strong>{{item.total}} <small>笔</small></strong></span><span>{{item.active}} 笔进行中<small v-if="data.show_fees">已结算 {{cash(item.settled)}} 积分</small></span></button></div>
  <div class="activity-summary-line"><span v-if="admin">涉及客户 <b>{{data.summary.customers||0}}</b> 位</span><span>注册已交付 <b>{{data.summary.delivered||0}}</b> 个</span><span>部分完成 <b>{{data.summary.partial||0}}</b> 笔</span><span>未完成 <b>{{data.summary.failed||0}}</b> 笔</span><span>已取消 <b>{{data.summary.cancelled||0}}</b> 笔</span></div>
  <section class="panel activity-records">
   <form class="activity-filters" @submit.prevent="apply">
    <label class="activity-search">搜索<div class="search-box"><Search :size="16"/><input v-model="search" :placeholder="admin?'订单号、客户邮箱、办理账号或项目':'订单号、办理账号或项目'" maxlength="200"/></div></label>
    <label>业务<select v-model="business"><option value="">全部业务</option><option v-for="(name,id) in names" :key="id" :value="id">{{name}}</option></select></label>
    <label>状态<select v-model="state"><option value="">全部状态</option><option v-for="(label,key) in states" :key="key" :value="key">{{label}}</option></select></label>
    <label>开始日期<input v-model="start" type="date" @change="period='custom'"/></label><label>结束日期<input v-model="end" type="date" @change="period='custom'" :min="start"/></label>
    <label v-if="admin" class="activity-admin-scope"><input v-model="includeAdmin" type="checkbox"/>包含管理员</label>
    <div class="activity-filter-actions"><button class="btn primary" :disabled="loading"><Filter :size="15"/>筛选</button><button type="button" class="btn secondary" :disabled="loading" @click="reset">重置</button></div>
   </form>
   <div class="activity-list-heading"><span>{{loading?'正在读取…':`共 ${data.total} 笔办理`}}</span><span v-if="data.show_fees">已结算 <b>{{cash(data.summary.settled)}}</b> · 冻结 <b>{{cash(data.summary.frozen)}}</b> 积分</span></div>
   <DataTable :rows="data.items" :columns="columns" :page-size="size" hide-toolbar hide-pagination>
    <template #record="{row}"><div class="activity-record"><span class="activity-business-label">{{names[row.business]}}</span><div><b>{{row.title}}</b><span>{{row.email||`${row.quantity} 个账号 · 已交付 ${row.delivered} 个`}}</span><small class="mono">{{row.id}}</small></div></div></template>
    <template #owner="{row}"><span class="activity-owner">{{row.owner}}</span></template>
    <template #progress="{row}"><span class="badge" :class="tone(row)">{{status(row)}}</span><SubscriptionRenewal :order="row"/><small v-if="row.business==='registration'">已交付 {{row.delivered}} / {{row.quantity}}</small></template>
    <template #fees="{row}"><b>{{cash(row.settled)}} <small>已结算</small></b><span v-if="row.frozen>0">{{cash(row.frozen)}} 冻结中</span><small v-else>{{['link','renewal'].includes(row.workflow)?'不收服务费':`报价 ${cash(row.fee)} 积分`}}</small></template>
    <template #time="{row}"><span>{{date(row.created_at)}}</span><small>更新 {{date(row.updated_at)}}</small></template>
    <template #duration="{row}"><OrderDuration :order="row"/></template>
    <template #action="{row}"><button class="text-btn" @click="openRecord(row)">查看</button></template>
   </DataTable>
   <div class="pagination"><label>每页 <select v-model.number="size" aria-label="每页条数" @change="page=1;load()"><option v-for="n in [10,25,50,100]" :key="n" :value="n">{{n}} 条</option></select></label><div><button class="icon-btn" aria-label="上一页" :disabled="loading||page<=1" @click="page--;load()"><ChevronLeft :size="16"/></button><span>{{page}} / {{pages}}</span><button class="icon-btn" aria-label="下一页" :disabled="loading||!data.has_more" @click="page++;load()"><ChevronRight :size="16"/></button></div></div>
  </section>
  <AdminActivityRecord v-if="admin&&record&&user?.role==='admin'" :record="record" :user="user" :pub="pub||{}" @close="record=null" @refresh="load(true)"/>
  <Modal v-if="detail" title="办理记录" @close="detail=null"><div class="activity-detail-heading"><h3>{{detail.title}}</h3><span class="badge" :class="tone(detail)">{{status(detail)}}</span></div><SubscriptionRenewal :order="detail"/><dl class="activity-detail"><dt>订单号</dt><dd class="mono">{{detail.id}}</dd><template v-if="admin"><dt>客户</dt><dd>{{detail.owner}}</dd></template><dt>办理账号</dt><dd>{{detail.email||`${detail.quantity} 个注册账号`}}</dd><template v-if="detail.business==='registration'"><dt>交付进度</dt><dd>{{detail.delivered}} / {{detail.quantity}}</dd></template><template v-if="data.show_fees"><dt>已结算</dt><dd>{{cash(detail.settled)}} 积分</dd><dt>冻结中</dt><dd>{{cash(detail.frozen)}} 积分</dd></template><dt>总耗时</dt><dd><OrderDuration :order="detail"/></dd><dt>提交时间</dt><dd>{{date(detail.created_at)}}</dd><dt>最近更新</dt><dd>{{date(detail.updated_at)}}</dd></dl><a v-if="detail.href" :href="detail.href" class="btn primary full">查看完整记录<ArrowUpRight :size="16"/></a></Modal>
 </section>
</template>
<style scoped>
.activity-summary-line{display:flex;flex-wrap:wrap;gap:12px 28px;margin:-8px 0 24px;color:var(--muted);font-size:12px}.activity-summary-line b{color:var(--ink);margin:0 4px}.activity-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin:24px 0 16px}.activity-stats button{display:flex;text-align:left;gap:14px;padding:23px;border:1px solid var(--line);border-radius:14px;background:var(--panel,#fff);color:var(--ink);cursor:pointer}.activity-stats svg{color:var(--green);margin-top:2px}.activity-stats span{font-size:12px;color:var(--muted)}.activity-stats strong{display:block;font-size:29px;color:var(--ink);margin:12px 0 6px;font-variant-numeric:tabular-nums}.activity-stats small{display:block;font-size:11px}.activity-stats button.selected,.activity-businesses button.selected{border-color:var(--green);background:#f0f8f4}.activity-businesses{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin-bottom:24px}.activity-businesses button{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:17px 22px;background:#fff;border:1px solid var(--line);border-radius:12px;cursor:pointer;text-align:left;font-size:12px;color:var(--muted)}.activity-businesses strong{display:block;font-size:22px;color:var(--ink);margin-top:8px}.activity-businesses small{display:block;margin-top:5px;font-size:11px}.activity-businesses strong small{display:inline;font-weight:400}.activity-filters{display:flex;flex-wrap:wrap;gap:14px;padding:22px;align-items:end}.activity-filters>label{display:grid;gap:8px;font-size:12px;flex:1;min-width:130px}.activity-filters .activity-search{flex:2;min-width:240px}.activity-search input{border:0;padding:0;min-width:0}.activity-search .search-box{width:100%;height:42px}.activity-filter-actions{display:flex;gap:8px}.activity-filters .activity-admin-scope{display:flex;flex:0 0 auto;min-width:0;align-items:center;white-space:nowrap;padding-bottom:12px}.activity-admin-scope input{width:16px;height:16px}.activity-list-heading{padding:16px 22px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:12px;color:var(--muted);font-size:12px}.activity-records{overflow:hidden}.activity-record{display:flex;gap:12px;align-items:flex-start;min-width:250px}.activity-record b,.activity-record span,.activity-records :deep(td>span:not(.badge)),.activity-records :deep(td>small),.activity-records :deep(td>b){display:block}.activity-record small{display:block;margin-top:7px;color:var(--muted);font-size:10px}.activity-record b{margin-bottom:6px}.activity-records :deep(td small){margin-top:7px;color:var(--muted);font-weight:400}.activity-business-label{background:#edf5f2;border-radius:6px;padding:5px 8px;font-size:11px;color:var(--green);white-space:nowrap}.activity-owner{max-width:230px;overflow-wrap:anywhere}.pagination select{width:auto;padding:6px}.pagination label{display:flex;align-items:center;gap:8px}.activity-detail-heading{display:flex;gap:12px;justify-content:space-between;align-items:center}.activity-detail{display:grid;grid-template-columns:90px minmax(0,1fr);gap:16px;margin:25px 0}.activity-detail dt{color:var(--muted);font-size:12px}.activity-detail dd{margin:0;overflow-wrap:anywhere;font-size:13px}@media(max-width:1000px){.activity-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.activity-businesses button{padding:14px;align-items:start;flex-direction:column}}@media(max-width:600px){.activity-stats{gap:10px}.activity-stats button{padding:16px;gap:9px}.activity-stats strong{font-size:25px}.activity-stats small{line-height:1.5}.activity-businesses{gap:8px}.activity-businesses button{padding:12px;font-size:11px}.activity-businesses strong{font-size:20px}.activity-filters{padding:16px;gap:12px}.activity-filters>label{min-width:calc(50% - 12px)}.activity-filters .activity-search{min-width:100%}.activity-list-heading{flex-direction:column}.activity-overview .page-heading{gap:12px}.activity-overview h1{font-size:26px}}

@media(max-width:700px){
.activity-records :deep(.table-scroll){overflow:visible}.activity-records :deep(table),.activity-records :deep(tbody){display:block;width:100%;min-width:0}.activity-records :deep(thead){display:none}.activity-records :deep(tr){display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px 18px;padding:18px 16px;border-bottom:1px solid var(--line)}.activity-records :deep(td){padding:0;border:0;max-width:none;min-width:0;font-size:12px}.activity-records :deep(td:first-child){grid-column:1/-1;margin-bottom:3px}.activity-records :deep(td:last-child){text-align:right;align-self:end}.activity-records :deep(td:last-child button){padding:8px 12px;background:#edf5f2;border-radius:7px}.activity-record{min-width:0}.activity-record b{font-size:13px}.activity-record span{overflow-wrap:anywhere}.activity-records :deep(td>span:not(.badge)){font-size:11px}.is-admin .activity-records :deep(td:nth-child(2)){grid-column:1/-1;padding-bottom:10px;border-bottom:1px dashed var(--line)}.is-admin .activity-records :deep(td:nth-child(2):before){content:'客户 ';font-size:11px;color:var(--muted)}.is-admin .activity-owner{display:inline!important}.activity-filters>label{margin-bottom:0}.activity-records :deep(td .badge){white-space:normal}
}
</style>

<style scoped>.activity-period{display:flex;gap:6px;align-items:center;flex-wrap:wrap}.activity-period button{padding:9px 15px;border-radius:7px;border:1px solid var(--line);background:white;color:var(--muted);cursor:pointer}.activity-period button.active{color:var(--green);background:#edf5f2;border-color:var(--green)}.activity-period span{font-size:11px;color:var(--muted);margin-left:8px}</style>
