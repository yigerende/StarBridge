<script setup lang="ts">
import {ref,computed,onMounted,onUnmounted} from 'vue'
import {Download,RefreshCw,Activity} from 'lucide-vue-next'
import {api,cash,type Row} from '../lib'
import BusinessTabs from './BusinessTabs.vue'
import DataTable from './DataTable.vue'
import RegistrationPanel from './RegistrationPanel.vue'
const props=defineProps<{user:Row;pub:Row}>()
const emit=defineEmits<{refresh:[];expired:[];navigate:[path:string]}>()
const today=new Date(Date.now()+8*3600000).toISOString().slice(0,10)
const initial={start:new Date(Date.parse(today)-29*86400000).toISOString().slice(0,10),end:today,period:'day',include_admin:'false'}
const draft=ref({...initial}),filter=ref({...initial}),overview=ref<Row>(),subscription=ref<Row>()
const business=ref<'subscription'|'registration'>('subscription'),registrationOpened=ref(false),loading=ref(false),error=ref('')
let controller:AbortController|undefined
const query=computed(()=>'?'+new URLSearchParams(filter.value))
const chartMax=computed(()=>Math.max(1,...(overview.value?.trend||[]).flatMap((t:Row)=>[Number(t.credited),Number(t.spent)])))
const metrics=computed(()=>[
 {label:'充值入账',value:overview.value?.totals.credited,note:'所选期间 · 共用钱包'},
 {label:'服务消费',value:overview.value?.totals.spent,note:'所选期间 · 订阅与注册合计'},
 {label:'可用余额',value:overview.value?.current.available,note:'当前所选账户可用积分'},
 {label:'冻结积分',value:overview.value?.current.frozen,note:'待结算订单预留，不计入消费'},
])
async function load(){
 controller?.abort();const current=new AbortController();controller=current;loading.value=true;error.value=''
 try{const [all,sub]=await Promise.all([api('/admin/finance/overview'+query.value,'GET',undefined,{},current.signal),api('/admin/finance'+query.value,'GET',undefined,{},current.signal)]);if(controller===current){overview.value=all;subscription.value=sub}}
 catch(e){if(controller===current&&(e as Error).name!=='AbortError')error.value=(e as Error).message}
 finally{if(controller===current)loading.value=false}
}
function apply(){filter.value={...draft.value};void load()}
function select(value:'subscription'|'registration'){business.value=value;if(value==='registration')registrationOpened.value=true}
onMounted(load);onUnmounted(()=>controller?.abort())
</script>
<template>
 <div class="page-heading"><div><span class="eyebrow">OPERATIONS / FINANCE</span><h1>资金中心</h1><p>统一查看钱包收支，按业务核对消费与交付成本。</p></div><button class="icon-btn bordered" :disabled="loading" @click="load" aria-label="刷新资金总览"><RefreshCw :size="17" :class="{spinning:loading}"/></button></div>
 <form class="panel finance-filters" @submit.prevent="apply">
  <label>开始日期<input type="date" v-model="draft.start" required aria-label="资金开始日期"/></label><label>结束日期<input type="date" v-model="draft.end" :min="draft.start" required aria-label="资金结束日期"/></label>
  <label>趋势粒度<select v-model="draft.period" aria-label="趋势统计周期"><option value="day">按天</option><option value="week">按周</option><option value="month">按月</option><option value="year">按年</option></select></label>
  <label class="checkbox-label"><input type="checkbox" v-model="draft.include_admin" true-value="true" false-value="false"/>包含管理员</label><button class="btn primary" :disabled="loading">{{loading?'更新中…':'查询'}}</button>
 </form>
 <div v-if="error" class="alert danger" role="alert">{{error}}<button @click="load">重试</button></div>
 <section aria-label="统一钱包总览" :aria-busy="loading"><div class="stats-grid four finance-metrics"><article class="stat" v-for="m in metrics" :key="m.label"><div class="stat-label">{{m.label}}</div><strong>{{overview?cash(m.value):'—'}}<small>积分</small></strong><span class="muted">{{m.note}}</span></article></div></section>
 <section class="panel chart-panel finance-chart"><div class="panel-heading"><h2>资金变动趋势</h2><div class="chart-legend"><span><i></i>充值入账</span><span><i></i>服务消费</span></div></div>
  <div v-if="overview?.trend.length" class="bar-chart"><div class="bar-group" v-for="t in overview.trend" :key="t.period" :title="`${t.period}：充值 ${cash(t.credited)} 积分，消费 ${cash(t.spent)} 积分`"><div class="bar-pair"><i :style="{height:(t.credited?Math.max(2,Number(t.credited)/chartMax*150):0)+'px'}"/><i :style="{height:(t.spent?Math.max(2,Number(t.spent)/chartMax*150):0)+'px'}"/></div><small>{{filter.period==='year'?t.period.slice(0,4):t.period.slice(5)}}</small></div></div>
  <div v-else class="empty compact-empty"><Activity :size="28"/><p>{{loading&&!overview?'正在读取资金记录…':'所选期间暂无资金流水'}}</p></div>
  <p class="muted small">{{overview?.start||filter.start}} 至 {{overview?.end||filter.end}} · 北京时间 · 人工调整 {{cash(overview?.totals.adjusted)}} 积分。充值与冻结不作为服务收入。</p>
 </section>
 <section class="finance-business"><div class="section-heading"><div><h2>业务明细</h2><p class="muted small">订阅与注册分别核算；上方钱包总览共用。</p></div></div><BusinessTabs :model-value="business" @update:model-value="select"/>
  <div v-show="business==='subscription'" class="finance-subscription">
   <div class="finance-detail-heading"><div><h3>订阅服务消费</h3><p class="muted small">所选期间已结算服务费 <b>{{cash(subscription?.totals.spent)}} 积分</b>；银行卡上游扣款另行结算。</p></div><a class="btn secondary" :href="'/api/admin/finance/export'+query"><Download :size="16"/>导出订阅报表</a></div>
   <div class="panel"><DataTable :rows="subscription?.users||[]" :columns="[{key:'email',label:'账户'},{key:'name',label:'称呼'},{key:'spent',label:'订阅消费（积分）'},{key:'orders',label:'订阅订单'},{key:'balance',label:'共用可用余额'},{key:'frozen',label:'共用冻结积分'},{key:'fee_override',label:'专属服务单价'}]"><template #spent="{row}">{{cash(row.spent)}}</template><template #balance="{row}">{{cash(Number(row.balance)-Number(row.frozen))}}</template><template #frozen="{row}">{{cash(row.frozen)}}</template><template #fee_override="{row}">{{row.fee_override==null?'按会员等级':cash(row.fee_override)+' 积分'}}</template></DataTable></div>
   <p class="muted small finance-note">账户充值与人工调整见统一钱包总览，完整账户明细保留在导出报表中。</p>
  </div>
  <RegistrationPanel v-if="registrationOpened" v-show="business==='registration'" route="/admin/finance" :enabled="pub.registration_enabled===true" :user="props.user" :pub="props.pub" :finance-filter="filter" @refresh="emit('refresh')" @expired="emit('expired')" @navigate="emit('navigate',$event)"/>
 </section>
</template>
<style scoped>
.finance-filters{display:flex;align-items:end;gap:16px;flex-wrap:wrap;padding:20px}.finance-filters label{margin:0;font-size:12px;min-width:140px}.finance-filters .checkbox-label{min-width:0;margin-bottom:12px}.finance-filters>.btn{margin-bottom:2px}.finance-metrics .muted{font-size:11px}.finance-chart .compact-empty{padding:28px}.finance-business{margin-top:32px}.finance-business .section-heading{margin:0 0 18px}.finance-business .section-heading p{margin:8px 0 0}.finance-detail-heading{display:flex;justify-content:space-between;align-items:center;gap:16px;margin:0 0 18px}.finance-detail-heading h3{font-size:17px;margin:0 0 10px}.finance-note{margin:16px 0;line-height:1.8}.finance-business :deep(.p-eyebrow){display:none}.finance-business :deep(.p-section-heading h2){font-size:18px}.finance-business :deep(.p-profit-equation){display:none}.finance-business :deep(.p-filter-bar){margin-top:0}.finance-business :deep(.p-performance-metrics article){box-shadow:none}.finance-business :deep(.p-filter-bar input){min-width:160px}
@media(max-width:760px){.finance-filters{padding:16px;gap:12px}.finance-filters label{flex:1;min-width:130px}.finance-detail-heading{align-items:flex-start;flex-direction:column}.finance-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.finance-metrics .stat{padding:18px}.finance-metrics .stat strong{font-size:24px}}
</style>
