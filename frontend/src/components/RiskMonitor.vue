<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref} from 'vue'
import {Ban,ChevronLeft,ChevronRight,Clock3,Filter,Plus,RefreshCw,Search,Settings2,ShieldAlert,ShieldCheck} from 'lucide-vue-next'
import {api,date,notify,type Row} from '../lib'
import Modal from './Modal.vue'
import RiskDiagnostics from './RiskDiagnostics.vue'
import '../risk-monitor.css'
const emit=defineEmits<{configure:[]}>()
const diagnostics=ref<InstanceType<typeof RiskDiagnostics>|null>(null)
function refresh(){if(['alerts','traffic'].includes(panel.value))void diagnostics.value?.refresh();else void load()}
const allTime=ref(new URLSearchParams(location.hash.split('?')[1]||'').get('all_time')==='true')
const data=ref<Row>({items:[],blocks:[],summary:{},categories:[]})
const q=ref(''),severity=ref(''),category=ref(''),state=ref('pending'),start=ref(''),end=ref(''),page=ref(1),size=ref(25)
const panel=ref('events'),loading=ref(false),busy=ref(false),error=ref(''),actionError=ref(''),detail=ref<Row|null>(null),confirmation=ref<Row|null>(null),blockForm=ref<Row|null>(null),blockSearch=ref('')
const applied=ref<Row>({all_time:String(allTime.value),state:'pending'})
const names:Row={access:'访问异常',login:'登录安全',limit:'频率限制',application:'订单异常',finance:'积分安全',admin:'管理操作',login_failed:'登录校验失败',login_rate:'登录限流',permission_denied:'权限拦截',rate_limit:'请求限流',order_rate:'下单限流',redeem_rate:'兑换限流'}
const levels:Row={high:'高风险',medium:'需关注',low:'一般'}
const pages=computed(()=>Math.max(1,Math.ceil((data.value.total||0)/size.value)))
const blocks=computed(()=>data.value.blocks.filter((b:Row)=>[b.ip,b.reason].some(v=>String(v).toLowerCase().includes(blockSearch.value.trim().toLowerCase()))))
let controller:AbortController|undefined
async function load(){
 controller?.abort();const request=new AbortController();controller=request;loading.value=true;error.value=''
 try{const value=await api('/admin/risk?'+new URLSearchParams({...applied.value,page:String(page.value),page_size:String(size.value)}),'GET',undefined,{},request.signal)
  if(controller===request){data.value=value;if(!applied.value.start)start.value=value.start;if(!applied.value.end)end.value=value.end;if(page.value>pages.value){page.value=pages.value;void load()}}}
 catch(e){if(controller===request&&(e as Error).name!=='AbortError')error.value=(e as Error).message}
 finally{if(controller===request)loading.value=false}
}
function filter(){if(!allTime.value&&start.value&&end.value&&start.value>end.value){error.value='结束日期不能早于开始日期';return}applied.value={all_time:String(allTime.value),q:q.value,severity:severity.value,category:category.value,state:state.value,start:start.value,end:end.value};page.value=1;void load()}
function reset(){q.value='';severity.value='';category.value='';state.value='pending';start.value='';end.value='';allTime.value=false;filter()}
async function resolve(row:Row){if(busy.value)return;busy.value=true;error.value='';try{await api('/admin/risk/'+row.id+'/resolve','POST',{});if(detail.value?.id===row.id)detail.value={...row,resolved:true};notify('已标记核查');await load()}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function release(){if(!confirmation.value||busy.value)return;busy.value=true;actionError.value='';try{await api('/admin/blocks/'+encodeURIComponent(confirmation.value.ip),'DELETE');confirmation.value=null;notify('IP 封禁已解除');await load()}catch(e){actionError.value=(e as Error).message}finally{busy.value=false}}
function openBlock(ip=''){actionError.value='';blockForm.value={ip,minutes:60,reason:''}}
async function block(){if(!blockForm.value||busy.value)return;busy.value=true;actionError.value='';try{await api('/admin/blocks','POST',blockForm.value);blockForm.value=null;notify('IP 已封禁');await load()}catch(e){actionError.value=(e as Error).message}finally{busy.value=false}}
onMounted(load);onUnmounted(()=>controller?.abort())
</script>
<template>
 <section class="risk-monitor">
  <div class="risk-toolbar"><span class="risk-policy"><ShieldCheck :size="16"/>按来源 IP 限流 · 管理员不限流</span><div class="actions"><button class="btn secondary" :disabled="loading||diagnostics?.loading" @click="refresh"><RefreshCw :size="15" :class="{'records-spin':loading||diagnostics?.loading}"/>刷新</button><button class="btn primary" @click="emit('configure')"><Settings2 :size="15"/>风控配置</button></div></div>
  <div class="risk-metrics">
   <article><span class="risk-metric-icon amber"><Clock3 :size="20"/></span><div><span>待核查事件</span><strong>{{data.summary.pending??'—'}}</strong><small>当前访问事件筛选范围</small></div></article>
   <article><span class="risk-metric-icon red"><ShieldAlert :size="20"/></span><div><span>高风险事件</span><strong>{{data.summary.high??'—'}}</strong><small>尚未核查的高风险记录</small></div></article>
   <article><span class="risk-metric-icon green"><Filter :size="20"/></span><div><span>触发次数</span><strong>{{data.summary.occurrences??'—'}}</strong><small>包含合并记录中的重复请求</small></div></article>
   <article><span class="risk-metric-icon blue"><Ban :size="20"/></span><div><span>已封禁 IP</span><strong>{{data.summary.blocks??'—'}}</strong><small>全站当前生效</small></div></article>
  </div>
  <p v-if="error" class="alert danger" role="alert">{{error}} <button class="text-btn" @click="load">重试</button></p>
  <div class="panel risk-workspace">
   <nav class="risk-navigation" aria-label="风控视图"><button v-for="tab in [{id:'events',name:'访问事件'},{id:'alerts',name:'业务告警'},{id:'blocks',name:'IP 封禁'},{id:'traffic',name:'访问统计'}]" :key="tab.id" :class="{active:panel===tab.id}" :aria-current="panel===tab.id?'page':undefined" @click="panel=tab.id">{{tab.name}}<span v-if="tab.id==='blocks'&&data.blocks.length">{{data.blocks.length}}</span></button></nav>
   <template v-if="panel==='events'">
    <form class="risk-query" @submit.prevent="filter">
     <label class="risk-search">搜索<div class="search-box"><Search :size="16"/><input v-model="q" placeholder="客户、IP 或事件说明" maxlength="200" aria-label="搜索访问事件"/></div></label>
     <label>事件分类<select v-model="category"><option value="">全部分类</option><option v-for="c in data.categories" :key="c" :value="c">{{names[c]||c}}</option></select></label>
     <label>风险级别<select v-model="severity"><option value="">全部级别</option><option v-for="(name,value) in levels" :key="value" :value="value">{{name}}</option></select></label>
     <label>处理状态<select v-model="state"><option value="">全部状态</option><option value="pending">待核查</option><option value="resolved">已核查</option></select></label>
     <div class="risk-query-row"><label class="risk-check"><input v-model="allTime" type="checkbox"/>全部时间</label><label>开始日期<input v-model="start" type="date" :disabled="allTime"/></label><span class="risk-date-divider">至</span><label>结束日期<input v-model="end" type="date" :disabled="allTime"/></label><div class="risk-query-actions"><button class="btn primary" :disabled="loading"><Search :size="15"/>查询</button><button type="button" class="btn secondary" :disabled="loading" @click="reset">重置</button></div></div>
    </form>
    <div class="risk-list-meta"><span>共 <b>{{data.total||0}}</b> 条事件 <span class="risk-meta-note">· {{applied.all_time==='true'?'全部时间':'北京时间'}} · 相同事件按分钟合并</span></span><span>{{loading?'正在刷新…':'更新于 '+date(data.generated_at)}}</span></div>
    <div class="table-scroll"><table class="risk-table"><thead><tr><th class="risk-event-col">事件</th><th>客户 / IP</th><th>最近发生</th><th>次数</th><th>状态</th><th>操作</th></tr></thead><tbody>
     <tr v-for="e in data.items" :key="e.id"><td data-label="事件" class="risk-wide"><div class="risk-event-title"><span class="risk-level" :class="e.severity">{{levels[e.severity]||e.severity}}</span><button class="risk-event-link" @click="detail=e">{{names[e.category]||e.category}}</button></div><p class="risk-description" :title="e.detail">{{e.detail}}</p></td><td data-label="客户 / IP"><b class="risk-customer">{{e.user_name||e.email||'未登录访问'}}</b><small v-if="e.user_name">{{e.email}}</small><small class="mono">{{e.ip||'—'}}</small></td><td data-label="最近发生"><time>{{date(e.last_seen)}}</time></td><td data-label="次数" class="risk-number">{{e.occurrences}}</td><td data-label="状态"><span class="risk-status" :class="{resolved:e.resolved}">{{e.resolved?'已核查':'待核查'}}</span></td><td data-label="操作"><div class="risk-row-actions"><button class="text-btn" @click="detail=e">详情</button><button v-if="!e.resolved" class="text-btn" :disabled="busy" @click="resolve(e)">标记核查</button></div></td></tr>
    </tbody></table><div v-if="!data.items.length" class="risk-empty"><ShieldCheck :size="30"/><strong>{{loading?'正在加载事件…':error?'事件加载失败':'没有符合条件的事件'}}</strong><span v-if="!loading&&!error">可调整日期、分类或处理状态后查询</span></div></div>
    <div class="risk-pagination"><label>每页<select v-model="size" @change="page=1;load()"><option :value="25">25 条</option><option :value="50">50 条</option><option :value="100">100 条</option></select></label><div><button class="icon-btn bordered" aria-label="上一页" :disabled="loading||page===1" @click="page--;load()"><ChevronLeft :size="16"/></button><span>{{page}} / {{pages}}</span><button class="icon-btn bordered" aria-label="下一页" :disabled="loading||page>=pages" @click="page++;load()"><ChevronRight :size="16"/></button></div></div>
   </template>
   <template v-else-if="panel==='blocks'">
    <div class="risk-section-heading"><div><h2>IP 封禁</h2><p>到期自动解除，也可手动解除。事件标记核查不会解除封禁。</p></div><button class="btn primary" @click="openBlock()"><Plus :size="15"/>封禁 IP</button></div>
    <div class="risk-block-search search-box"><Search :size="16"/><input v-model="blockSearch" placeholder="搜索 IP 或封禁原因" aria-label="搜索封禁 IP"/></div>
    <div class="table-scroll"><table class="risk-table"><thead><tr><th>IP 地址</th><th>封禁原因</th><th>自动解除时间</th><th>操作</th></tr></thead><tbody><tr v-for="b in blocks" :key="b.ip"><td data-label="IP 地址" class="mono">{{b.ip}}</td><td data-label="封禁原因">{{b.reason}}</td><td data-label="自动解除时间">{{date(b.expires_at)}}</td><td data-label="操作"><button class="text-btn" :disabled="busy" @click="actionError='';confirmation=b">解除封禁</button></td></tr></tbody></table><div v-if="!blocks.length" class="risk-empty"><ShieldCheck :size="30"/><strong>{{blockSearch?'没有匹配的 IP':'当前没有封禁的 IP'}}</strong></div></div>
   </template>
   <RiskDiagnostics v-else ref="diagnostics" :key="panel" :mode="panel"/>
  </div>
  <Modal v-if="detail" title="风险事件" @close="detail=null"><div class="risk-dialog"><span class="risk-level" :class="detail.severity">{{levels[detail.severity]||detail.severity}}</span><h3>{{names[detail.category]||detail.category}}</h3><p class="risk-full-description">{{detail.detail}}</p><dl><dt>客户</dt><dd>{{detail.user_name||detail.email||'未登录访问'}}</dd><dt>IP 地址</dt><dd class="mono">{{detail.ip||'—'}}</dd><dt>首次发生</dt><dd>{{date(detail.created_at)}}</dd><dt>最近发生</dt><dd>{{date(detail.last_seen)}}</dd><dt>触发次数</dt><dd>{{detail.occurrences}}</dd><dt>处理状态</dt><dd>{{detail.resolved?'已核查':'待核查'}}</dd></dl><div class="risk-dialog-actions"><button v-if="!detail.resolved" class="btn primary" :disabled="busy" @click="resolve(detail)">标记已核查</button><button v-if="detail.ip" class="btn secondary" @click="openBlock(detail.ip);detail=null">封禁此 IP</button></div></div></Modal>
  <Modal v-if="confirmation" title="解除 IP 封禁" @close="!busy&&(confirmation=null)"><p>确认解除 <b class="mono">{{confirmation.ip}}</b> 的封禁？</p><p v-if="actionError" class="alert danger" role="alert">{{actionError}}</p><div class="risk-dialog-actions"><button class="btn secondary" :disabled="busy" @click="confirmation=null">取消</button><button class="btn primary" :disabled="busy" @click="release">{{busy?'正在解除…':'确认解除'}}</button></div></Modal>
  <Modal v-if="blockForm" title="封禁 IP" @close="!busy&&(blockForm=null)"><form class="risk-block-form" @submit.prevent="block"><p class="muted">该 IP 的访问将被拦截，请确认地址和封禁时长。</p><label>IP 地址<input v-model.trim="blockForm.ip" required maxlength="45" placeholder="IPv4 或 IPv6 地址"/></label><label>封禁时长（分钟）<input v-model.number="blockForm.minutes" type="number" min="1" max="10080" required/></label><label>封禁原因<textarea v-model.trim="blockForm.reason" rows="3" minlength="2" maxlength="300" required placeholder="填写封禁原因"/></label><p v-if="actionError" class="alert danger" role="alert">{{actionError}}</p><div class="risk-dialog-actions"><button type="button" class="btn secondary" :disabled="busy" @click="blockForm=null">取消</button><button class="btn primary" :disabled="busy">{{busy?'正在保存…':'确认封禁'}}</button></div></form></Modal>
 </section>
</template>
