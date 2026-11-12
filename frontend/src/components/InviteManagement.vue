<script setup lang="ts">
import {ref,onMounted} from 'vue'
import {Search,RefreshCw} from 'lucide-vue-next'
import {api,cash,date,notify,type Row} from '../lib'
import DataTable from './DataTable.vue'
import ListPager from './ListPager.vue'
import {rememberList,toggleSort} from '../listState'
const emit=defineEmits<{changed:[]}>()
const data=ref<Row>({items:[],stats:{}}),rules=ref<Row>({enabled:true,stages:[]}),page=ref(1),search=ref(''),busy=ref(false),saving=ref(false),error=ref('')
const size=ref(10),sortBy=ref('created_at'),sortDir=ref('desc'),rewardState=ref('')
rememberList('admin-invitations',{page,size,sortBy,sortDir,search,rewardState})
function changeSort(field:string){toggleSort(field,sortBy,sortDir,page);void load()}

const states:Row={waiting:'待满足条件',rewarded:'已结算',adjusted:'部分退回',reversed:'已撤回',recovering:'待扣回'}
async function load(initial=false){busy.value=true;error.value='';try{data.value=await api('/admin/invites?'+new URLSearchParams({page:String(page.value),page_size:String(size.value),q:search.value,state:rewardState.value,sort_by:sortBy.value,sort_dir:sortDir.value}));if(initial)rules.value=JSON.parse(JSON.stringify(data.value.rules))}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function save(){saving.value=true;try{await api('/admin/settings/invites','PUT',rules.value);notify('奖励规则已保存，仅影响新建订单');await load(true);emit('changed')}catch(e){notify((e as Error).message)}finally{saving.value=false}}
onMounted(()=>load(true))
</script>
<template>
 <div v-if="error" class="alert danger">{{error}}<button @click="load()">重试</button></div>
 <form class="panel invitation-rules" @submit.prevent="save"><div><h2>伙伴奖励规则</h2><p>从被邀请人注册起分三个阶段持续返积分；达到最后一期天数后，新订单不再奖励。</p></div><label class="checkbox-label"><input v-model="rules.enabled" type="checkbox"/>开放邀请奖励</label><div v-for="(stage,i) in rules.stages" :key="i"><label>第 {{Number(i)+1}} 阶段截止天数<input v-model.number="stage.until_days" type="number" min="1" max="365" required/></label><label>奖励比例（%）<input v-model.number="stage.rate_percent" type="number" min="1" max="30" step="1" required/></label></div><button class="btn primary" :disabled="saving">{{saving?'保存中…':'保存规则'}}</button><p class="rules-note">天数递增，比例逐段降低（1%～30%）；下单时按注册时长锁定。默认未满30天10%、30～未满60天5%、60～未满90天2%、满90天停止；关闭仅影响新建订单，已符合条件的订单继续结算。历史订单不补发，退款按比例撤回。可用余额不足的奖励保留待扣回，不占用冻结服务费。通常一分钟内自动结算。</p></form>
 <div class="stats-grid four"><article class="stat" v-for="s in [{k:'invited',n:'邀请关系',u:'位'},{k:'qualified',n:'获奖伙伴',u:'位'},{k:'earned',n:'有效奖励',u:'积分',money:true},{k:'pending_recovery',n:'待扣回奖励',u:'积分',money:true}]" :key="s.k"><div class="stat-label">{{s.n}}</div><strong>{{s.money?cash(data.stats[s.k]):data.stats[s.k]||0}}<small>{{s.u}}</small></strong></article></div>
 <form class="records-filters" @submit.prevent="page=1;load()"><input v-model="search" maxlength="200" placeholder="搜索邮箱" aria-label="搜索邀请记录"/><select v-model="rewardState" aria-label="奖励状态"><option value="">全部奖励状态</option><option v-for="(name,id) in states" :value="id">{{name}}</option></select><button class="btn secondary" :disabled="busy">查询</button></form><div class="panel"><DataTable paged :sort-by="sortBy" :sort-dir="sortDir" @sort="changeSort" :rows="data.items" :searchable="false" hide-pagination :page-size="25" :columns="[{key:'inviter',label:'邀请人'},{key:'email',label:'被邀请人'},{key:'created_at',label:'注册时间'},{key:'reward_state',label:'奖励状态'},{key:'fee',label:'累计参与服务费'},{key:'rate_percent',label:'历史最高比例'},{key:'entitled',label:'有效奖励'},{key:'recovered',label:'已扣回'},{key:'rewarded_at',label:'结算时间'}]"><template #created_at="{row}">{{date(row.created_at)}}</template><template #reward_state="{row}"><span class="badge" :class="row.reward_state==='recovering'?'amber':''">{{states[row.reward_state]}}</span></template><template #rate_percent="{row}">{{row.rate_percent==null?'—':row.rate_percent+'%'}}</template><template v-for="field in ['fee','entitled','recovered']" #[field]="{row}">{{row[field]==null?'—':cash(row[field])+' 积分'}}</template><template #rewarded_at="{row}">{{date(row.rewarded_at)}}</template></DataTable></div>
 <ListPager v-model:page="page" v-model:size="size" :total="data.total||0" :loading="busy" @change="load"/>
</template>
<style scoped>.invitation-rules{padding:24px;margin-bottom:24px;display:flex;align-items:center;gap:20px;flex-wrap:wrap}.invitation-rules>div{flex:1;min-width:240px}.invitation-rules h2{font-size:16px;margin:0 0 8px}.invitation-rules p{font-size:12px;color:#718492;line-height:1.8;margin:0}.invitation-rules input[type=number]{width:100px}.invitation-rules .rules-note{width:100%;border-top:1px solid #edf1f4;padding-top:16px}</style>
