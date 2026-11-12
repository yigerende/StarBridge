<script setup lang="ts">
import {computed,onMounted,ref,watch} from 'vue'
import {api,copy,date,cash,type Row} from '../lib'
import {useRemoteList} from '../useRemoteList'
import ListPager from './ListPager.vue'
import SortHeader from './SortHeader.vue'
import Modal from '../refund/Modal.vue'
const props=defineProps<{selected:Row|null;initialTab?:string}>(),emit=defineEmits<{close:[];snapshot:[Row];changed:[]}>()
const root='/admin/refund/mother-management',items=ref<Row[]>([]),settings=ref<Row>({auto_refresh:true}),busy=ref(false),error=ref(''),tab=ref('details'),credentials=ref<Row|null>(null),pending=ref<Row|null>(null),session=ref('')
const q=ref(''),seat=ref(''),state=ref('')
const list=useRemoteList('refund-mother-members',()=>props.selected&&tab.value==='members'?`${root}/${props.selected.id}/members`:'',{q,seat_type:seat,status:state},{sortBy:'email',sortDir:'asc'})
const {rows,page,size,sortBy,sortDir,data,loading,error:listError}=list
const selectedInfo=computed(()=>items.value.find(r=>r.id===props.selected?.id))
const seatEstimate=computed(()=>{const end=Date.parse(selectedInfo.value?.management_json?.plan?.expires_at||'');return Number.isFinite(end)?(125/30*Math.max(0,Math.floor((end+28800000)/86400000)-Math.floor((Date.now()+28800000)/86400000))).toFixed(2):'—'})
const metrics=computed(()=>{const v={standard:0,premium:0,standardTotal:0,premiumTotal:0,held:0,loaded:0,completed:0};for(const r of items.value){const c=r.management_json?.capacity;v.completed+=r.completed||0;if(c){v.loaded++;v.standard+=c.standard.remaining;v.premium+=c.premium.remaining;v.standardTotal+=c.standard.total;v.premiumTotal+=c.premium.total;v.held+=c.premium.held}}return v})
async function load(){const d=await api(root+'/summary');items.value=d.items;emit('snapshot',Object.fromEntries(d.items.map((r:Row)=>[r.id,r])))}
async function perform(fn:()=>Promise<void>){busy.value=true;error.value='';try{await fn()}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function refresh(kind:string,id?:string){await perform(async()=>{const failures:string[]=[];for(const r of id?[{id,name:props.selected?.name}]:items.value){try{await api(`${root}/${r.id}/${kind}`,'POST',{})}catch(e){failures.push(`${r.name}：${(e as Error).message}`)}}await load();emit('changed');if(failures.length)error.value=failures.join('；')})}
async function reveal(){const id=props.selected?.id;tab.value='credentials';await perform(async()=>{const value=await api(`${root}/${id}/credentials`);if(props.selected?.id===id&&tab.value==='credentials')credentials.value=value})}
async function saveSettings(){await perform(async()=>{settings.value=await api(root+'/settings','PUT',settings.value)})}
async function saveChild(){await perform(async()=>{await api(root+'/children','POST',{session:session.value});session.value='';await list.load()})}
async function remove(){const v=pending.value;if(!v)return;await perform(async()=>{await api(`${root}/${props.selected?.id}/members/remove`,'POST',{email:v.email,user_id:v.id,team_id:props.selected?.team_id,method:v.method});pending.value=null;await list.load()})}
watch(()=>props.selected?.id,()=>{tab.value=props.initialTab||'details';credentials.value=null;pending.value=null;session.value='';error.value='';q.value='';page.value=1})
watch(data,()=>{void load().catch(e=>{error.value=(e as Error).message})})
watch(tab,()=>{credentials.value=null;pending.value=null;session.value=''})
onMounted(()=>perform(async()=>{await load();settings.value=await api(root+'/settings')}))
defineExpose({load})
</script>
<template>
 <div class="mother-tools">
  <div class="mother-metrics"><article><span>普通席位剩余</span><strong>{{metrics.loaded?metrics.standard:'—'}}<small> / {{metrics.loaded?metrics.standardTotal:'—'}}</small></strong></article><article><span>高级席位剩余</span><strong>{{metrics.loaded?metrics.premium:'—'}}<small> / {{metrics.loaded?metrics.premiumTotal:'—'}}</small></strong></article><article><span>高级临停席位</span><strong>{{metrics.loaded?metrics.held:'—'}}</strong></article><article><span>累计完成代退</span><strong>{{metrics.completed}}<small> 笔</small></strong></article></div>
  <div class="mother-toolbar"><small>席位为上次查询快照 · 已查询 {{metrics.loaded}} / {{items.length}} 个母号</small><button class="btn secondary" :disabled="busy||!items.length" @click="refresh('capacity')">刷新全部席位</button><button class="btn secondary" :disabled="busy||!items.length" @click="refresh('plan')">刷新全部套餐</button><label class="checkbox-label"><input v-model="settings.auto_refresh" type="checkbox" :disabled="busy" @change="saveSettings"/>空闲时自动刷新即将过期的 AT</label></div>
  <p v-if="error&&!selected" class="alert danger" role="alert">{{error}}</p>
 </div>
 <Modal v-if="selected" :title="selected.name+' · 母号管理'" wide :busy="busy" @close="!busy&&emit('close')">
  <div class="mother-detail">
   <p class="muted">{{selected.email}} <span class="mono">{{selected.team_id}}</span></p>
   <nav class="tabs"><button :class="{active:tab==='details'}" @click="tab='details'">套餐与席位</button><button :class="{active:tab==='members'}" @click="tab='members'">团队成员</button><button :class="{active:tab==='credentials'}" :disabled="busy" @click="reveal">查看 AT / RT</button></nav>
   <p v-if="error" class="alert danger" role="alert">{{error}}</p>
   <template v-if="tab==='details'">
    <p v-if="selectedInfo?.management_json?.refresh_pending" class="alert danger">上次凭据刷新结果不明，请在编辑母号中重新录入有效凭据。</p>
    <div class="mother-toolbar"><button class="btn secondary" :disabled="busy" @click="refresh('capacity',selected.id)">刷新席位</button><button class="btn secondary" :disabled="busy" @click="refresh('plan',selected.id)">刷新套餐</button><button class="btn secondary" :disabled="busy||!selected.has_refresh" @click="refresh('refresh',selected.id)">刷新 AT / RT</button></div>
    <div class="detail-grid"><div><span>套餐</span><strong>{{selectedInfo?.management_json?.plan?.subscription_plan||selected.plan_type||'未查询'}}</strong></div><div><span>套餐到期</span><strong>{{date(selectedInfo?.management_json?.plan?.expires_at)}}</strong></div><div><span>续期时间</span><strong>{{date(selectedInfo?.management_json?.plan?.renews_at)}}</strong></div><div><span>套餐查询时间</span><strong>{{date(selectedInfo?.management_json?.plan?.checked_at)}}</strong></div><div><span>高级席位参考价（USD）</span><strong>{{seatEstimate}}</strong><small class="block">125 ÷ 30 × 剩余天数，仅供参考</small></div><div><span>最近刷新 AT</span><strong>{{date(selectedInfo?.last_refreshed_at)}}</strong></div><div><span>累计办理 / 完成</span><strong>{{selectedInfo?.handled||0}} / {{selectedInfo?.completed||0}} 笔</strong></div><div><span>已结算服务费</span><strong>{{cash(selectedInfo?.settled)}} 积分</strong></div></div>
    <div class="table-scroll"><table><thead><tr><th>席位类型</th><th>总量</th><th>剩余</th><th>临停</th></tr></thead><tbody><tr v-for="[key,label] in [['standard','普通席位'],['premium','高级席位（5x）']]" :key="key"><td>{{label}}</td><td>{{selectedInfo?.management_json?.capacity?.[key]?.total??'—'}}</td><td>{{selectedInfo?.management_json?.capacity?.[key]?.remaining??'—'}}</td><td>{{selectedInfo?.management_json?.capacity?.[key]?.held??'—'}}</td></tr></tbody></table></div><p class="muted">席位查询：{{date(selectedInfo?.management_json?.capacity?.checked_at)}}</p>
   </template>
   <template v-else-if="tab==='members'">
    <div class="table-toolbar"><input v-model="q" type="search" placeholder="搜索成员邮箱或名称" maxlength="200"/><select v-model="seat"><option value="">全部席位</option><option value="default">普通席位</option><option value="prolite">高级席位</option></select><select v-model="state"><option value="">全部状态</option><option value="active">正常</option><option value="inactive">停用</option></select><button class="btn secondary" :disabled="loading" @click="list.load">刷新成员</button></div>
    <p v-if="listError" class="alert danger">{{listError}}</p>
    <div class="table-scroll"><table><thead><tr><SortHeader v-for="[field,label] in [['email','成员'],['role','角色'],['seat_type','席位'],['created_time','加入时间'],['active','状态']]" :key="field" :field="field" :label="label" :sort-by="sortBy" :sort-dir="sortDir" @sort="list.sort"/><th>操作</th></tr></thead><tbody><tr v-for="r in rows" :key="r.id"><td><span>{{r.email}}</span><small class="block">{{r.name}}</small></td><td>{{r.role==='account-owner'?'所有者':r.role==='standard-user'?'成员':r.role}}</td><td>{{r.seat_type==='prolite'?'高级':r.seat_type==='default'?'普通':r.seat_type}}</td><td>{{date(r.created_time)}}</td><td>{{r.active?'正常':'已停用'}}</td><td><div class="mother-actions"><button class="link" :disabled="busy||!r.can_kick" @click="pending={...r,method:'mother_kick'}">母号移出</button><button class="link" :disabled="busy||!r.can_leave" @click="pending={...r,method:'child_leave'}">子号退出</button></div></td></tr><tr v-if="!rows.length"><td colspan="6" class="empty-state">{{loading?'正在查询成员…':'没有符合条件的成员'}}</td></tr></tbody></table></div>
    <ListPager v-model:page="page" v-model:size="size" :total="data.total||0" :loading="loading"/>
    <div v-if="pending" class="confirm-box"><strong>确认{{pending.method==='child_leave'?'让子号退出':'移出成员'}} {{pending.email}}？</strong><p>操作会改变上游团队成员关系。</p><button class="btn danger" :disabled="busy" @click="remove">确认操作</button><button class="btn secondary" :disabled="busy" @click="pending=null">返回</button></div>
    <details><summary>导入子号凭据</summary><p class="muted">使用“子号退出”前，导入该子号的 Session JSON 或 AT。</p><textarea v-model="session" rows="4" autocomplete="off" spellcheck="false" placeholder="Session JSON / Access Token"/><button class="btn secondary" :disabled="busy||!session.trim()" @click="saveChild">保存凭据</button></details>
   </template>
   <template v-else-if="credentials"><label>Access Token<textarea :value="credentials.access_token" readonly rows="5" spellcheck="false"/></label><button class="btn secondary" @click="copy(credentials.access_token)">复制 AT</button><label>Refresh Token<textarea :value="credentials.refresh_token||'未保存 RT'" readonly rows="3" spellcheck="false"/></label><button class="btn secondary" :disabled="!credentials.refresh_token" @click="copy(credentials.refresh_token)">复制 RT</button><p class="muted">AT 到期：{{date(credentials.expires_at)}}</p></template>
  </div>
 </Modal>
</template>
<style scoped>
.mother-tools{margin-bottom:18px}.mother-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.mother-metrics article{padding:17px 20px;border:1px solid var(--line);border-radius:12px;background:var(--surface)}.mother-metrics span,.detail-grid span{display:block;color:var(--muted);font-size:12px;margin-bottom:8px}.mother-metrics strong{font-size:24px;font-variant-numeric:tabular-nums}.mother-metrics small{font-size:12px;font-weight:400}.mother-toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:16px 0}.mother-toolbar>small{margin-right:auto;color:var(--muted)}.mother-detail{display:grid;gap:14px;min-width:0}.mother-detail>p{margin:0;overflow-wrap:anywhere}.mother-detail textarea{width:100%;margin:8px 0;resize:vertical}.mother-actions{display:flex;gap:12px;white-space:nowrap}.detail-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.detail-grid>div{padding:12px 0;border-bottom:1px solid var(--line)}.detail-grid strong{font-size:14px}.confirm-box{border:1px solid var(--line);border-radius:10px;padding:16px}.confirm-box button{margin-right:10px}details summary{cursor:pointer;font-weight:600;padding:12px 0}@media(max-width:650px){.mother-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.detail-grid{grid-template-columns:1fr}.mother-detail table{min-width:720px}}
</style>
