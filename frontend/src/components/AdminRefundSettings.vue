<script setup lang="ts">
import {useRemoteList} from '../useRemoteList'
import {rememberList} from '../listState'
import ListPager from './ListPager.vue'
import SortHeader from './SortHeader.vue'
import RefundMotherTools from './RefundMotherTools.vue'
import RefundResourceRecovery from './RefundResourceRecovery.vue'
import RefundAccountRestrictions from './RefundAccountRestrictions.vue'
import ProxyCooldown from "./ProxyCooldown.vue";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import {api as bridgeAPI,labels as statuses,cash} from '../lib'
async function api<T=any>(path:string,options?:{method?:string;body?:string}):Promise<T>{return bridgeAPI(path,options?.method||'GET',options?.body?JSON.parse(options.body):undefined) as Promise<T>}
import ResourceCheckSummary from "../refund/ResourceCheckSummary.vue";
import type { ResourceCheck } from "../refund/resource-check";
import Modal from "../refund/Modal.vue";

import { parseVerificationStatuses } from "../refund/admin-verification-statuses";
type ProxyRow = { recovery_successes?:number;recovery_required?:number|null;probe_until?:string; target?:string; failure_until?:string; failure_code?:string; check?:ResourceCheck; id:string; name:string; enabled:boolean; has_secret:boolean; url?:string };
type Mother = { auto_disabled_reason?:string;auto_disabled_at?:string; auth_blocked?:boolean; check?:ResourceCheck; id:string; name:string; email:string; team_id:string; proxy_id:string; seat_type:string; enabled:boolean; has_secret:boolean; access_token?:string; session?:string; refresh_token?:string; has_refresh?:boolean; plan_type?:string; token_expires_at?:string; active_refund?:string; active_email?:string; active_status?:string };
type CredentialPreview = {email:string;team_id:string;plan_type:string;expires_at:string|null;has_refresh:boolean};
type Recovery={verification_http_statuses?:number[];plan_fallback?:{enabled:boolean;max_cycles:number;max_attempts:number;retry_seconds:number};enabled:boolean;max_retries:number;verify_intervals:number[];cleanup_intervals:number[];alert_seconds:number;cleanup_failed:boolean};
type Settings = { deactivated_resubmit_limit:number; child_proxy_recovery_interval_seconds:number; child_proxy_recovery_success_count:number|null|''; query_personal_plan:boolean;resource_recovery:{team_session_failure_limit:number;mother_failed_accounts:number;account_failure_limit:number;post_login_wait_seconds:number;fallback_plan_wait_seconds:number;failure_limit:number;cooldown_seconds:number;watch_seconds:number;retry_seconds:number;slow_retry_seconds:number;fast_attempts:number;login_failure_limit:number;login_window_seconds:number;login_cooldown_seconds:number}; child_proxy_backups:string[];child_proxy_cooldown_seconds:number; mother_failover:{enabled:boolean;max_switches:number;retry_seconds:number}; recovery:Recovery; enabled:boolean; max_parallel:number; max_attempts:number; retry_seconds:number; child_proxy_id:string; mother_interval_seconds:number };
const section=ref('mothers'),runtimeReady=ref(false);
const emit=defineEmits<{toast:[message:string]}>();
const resourceDefaults={team_session_failure_limit:3,mother_failed_accounts:2,account_failure_limit:4,post_login_wait_seconds:5,fallback_plan_wait_seconds:5,failure_limit:3,cooldown_seconds:300,watch_seconds:60,retry_seconds:30,slow_retry_seconds:300,fast_attempts:3,login_failure_limit:3,login_window_seconds:60,login_cooldown_seconds:30};
const settingsRevision=ref(0);
const settings=ref<Settings>({deactivated_resubmit_limit:0,child_proxy_recovery_interval_seconds:5,child_proxy_recovery_success_count:3,query_personal_plan:true,resource_recovery:{...resourceDefaults},child_proxy_backups:[],child_proxy_cooldown_seconds:300,mother_failover:{enabled:true,max_switches:3,retry_seconds:30},recovery:{plan_fallback:{enabled:true,max_cycles:2,max_attempts:3,retry_seconds:30},enabled:true,max_retries:3,verify_intervals:[30,60,120,300],cleanup_intervals:[15,30,60],alert_seconds:600,cleanup_failed:true},enabled:false,max_parallel:2,max_attempts:2,retry_seconds:30,child_proxy_id:"",mother_interval_seconds:0});
const intervalText=(key:'verify_intervals'|'cleanup_intervals')=>computed({get:()=>settings.value.recovery[key].join(', '),set:(value:string)=>{settings.value.recovery[key]=value.split(/[,，\s]+/).filter(Boolean).map(Number)}});
const verifyIntervals=intervalText('verify_intervals'),cleanupIntervals=intervalText('cleanup_intervals');
const verificationStatuses=ref("404");
const mothers=ref<Mother[]>([]),proxies=ref<ProxyRow[]>([]),busy=ref(false),error=ref(""),formError=ref(""),modal=ref("");
const resourceSummary=ref<Record<string,{total:number;enabled:number}>>({})
rememberList('refund-resource-section',{section})
const motherQuery=ref(''),motherStatus=ref(''),proxyQuery=ref(''),proxyStatus=ref(''),proxyTarget=ref('')
const motherList=useRemoteList('refund-mothers',()=>section.value==='mothers'?'/admin/refund/resources/mothers':'',{q:motherQuery,status:motherStatus},{sortBy:'name',sortDir:'asc'})
const proxyList=useRemoteList('refund-proxies',()=>section.value==='proxies'?'/admin/refund/resources/proxies':'',{q:proxyQuery,status:proxyStatus,target:proxyTarget},{sortBy:'name',sortDir:'asc'})
const {page:motherPage,size:motherSize,sortBy:motherSort,sortDir:motherDir,data:motherData,rows:motherRows,loading:motherLoading,error:motherError}=motherList
const {page:proxyPage,size:proxySize,sortBy:proxySort,sortDir:proxyDir,data:proxyData,rows:proxyRows,loading:proxyLoading,error:proxyError}=proxyList
const mother=ref<Mother>({id:"",name:"",email:"",team_id:"",proxy_id:"",seat_type:"default",enabled:true,has_secret:false,access_token:""});
const proxy=ref<ProxyRow>({id:"",name:"",url:"",target:"both",enabled:true,has_secret:false});
const motherProxies=computed(()=>proxies.value.filter(p=>p.target!=='child'));
const childProxies=computed(()=>proxies.value.filter(p=>p.target!=='mother'));
const targetName=(p:ProxyRow)=>p.target==='mother'?'母号':p.target==='child'?'子号':'母号 / 子号';
function selectBackup(id:string,checked:boolean){settings.value.child_proxy_backups=checked?[...settings.value.child_proxy_backups,id]:settings.value.child_proxy_backups.filter(x=>x!==id)}
function moveBackup(id:string,by:number){const a=[...settings.value.child_proxy_backups],i=a.indexOf(id),to=i+by;if(to>=0&&to<a.length){[a[i],a[to]]=[a[to],a[i]];settings.value.child_proxy_backups=a}}
watch(()=>settings.value.child_proxy_id,id=>{settings.value.child_proxy_backups=settings.value.child_proxy_backups.filter(x=>x!==id)});
const managedMother=ref<Mother|null>(null),motherSnapshots=ref<Record<string,any>>({}),managedTab=ref('details'),motherTools=ref<InstanceType<typeof RefundMotherTools>>();
const snapshot=(m:Mother)=>motherSnapshots.value[m.id]?.management_json||{};
function seats(m:Mother,key:string){const value=snapshot(m).capacity,check=m.check;return value&&(!check?.checked_at||Date.parse(value.checked_at)>=Date.parse(check.checked_at))?value[key]:check?.seats?.[key as 'standard'|'premium']}
function seatPrice(m:Mother){const expiry=Date.parse(snapshot(m).plan?.expires_at||'');if(!Number.isFinite(expiry))return '—';const days=Math.max(0,Math.floor((expiry+28800000)/86400000)-Math.floor((Date.now()+28800000)/86400000));return '$'+(125/30*days).toFixed(2)}
function manage(m:Mother,tab='details'){managedTab.value=tab;managedMother.value=m}
async function changeMother(m:Mother,changes:{enabled?:boolean;proxy_id?:string}){busy.value=true;error.value='';try{await api(`/admin/refund/mother-management/${m.id}/resource`,{method:'PATCH',body:JSON.stringify(changes)});await load();emit('toast','母号设置已保存')}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
const removing=ref<{kind:string;id:string;name:string}>();
const motherCheck=ref<ResourceCheck>(),proxyCheck=ref<ResourceCheck>();
const motherPreview=ref<CredentialPreview>(),parsing=ref(false),fileInput=ref<HTMLInputElement>();
watch(()=>mother.value.session,()=>{motherPreview.value=undefined});
watch(()=>[mother.value.session,mother.value.access_token,mother.value.team_id,mother.value.email,mother.value.proxy_id],()=>{motherCheck.value=undefined});
watch(()=>proxy.value.url,()=>{proxyCheck.value=undefined});
const savedCredential=ref(''),savedRefresh=ref(''),loadingCredential=ref(false);
let credentialRequest=0;
const motherInput=()=>{const m=mother.value;return {id:m.id,name:m.name,email:m.email,team_id:m.team_id,proxy_id:m.proxy_id,seat_type:m.seat_type,enabled:m.enabled,session:(m.session||'')===savedCredential.value?'':m.session||'',refresh_token:(m.refresh_token||'')===savedRefresh.value?'':m.refresh_token||''}};
const proxyInput=()=>{const p=proxy.value;return {id:p.id,name:p.name,url:p.url||"",target:p.target,enabled:p.enabled}};
const displayTime=(value:string)=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
async function load(){
 busy.value=true;error.value="";
 try{const d=await api<{settings:Settings;mothers:Mother[];proxies:ProxyRow[];runtime_ready:boolean;resource_summary:Record<string,{total:number;enabled:number}>}>("/admin/refund/settings?summary_only=true");runtimeReady.value=d.runtime_ready;settings.value={...d.settings,deactivated_resubmit_limit:d.settings.deactivated_resubmit_limit??0,child_proxy_recovery_interval_seconds:d.settings.child_proxy_recovery_interval_seconds??5,child_proxy_recovery_success_count:d.settings.child_proxy_recovery_success_count===undefined?3:d.settings.child_proxy_recovery_success_count,query_personal_plan:d.settings.query_personal_plan!==false,resource_recovery:{...resourceDefaults,...d.settings.resource_recovery},child_proxy_backups:d.settings.child_proxy_backups||[],child_proxy_cooldown_seconds:d.settings.child_proxy_cooldown_seconds||300};settings.value.recovery.plan_fallback??={enabled:true,max_cycles:2,max_attempts:3,retry_seconds:30};verificationStatuses.value=(settings.value.recovery.verification_http_statuses??[404]).join(',');resourceSummary.value=d.resource_summary;mothers.value=d.mothers;proxies.value=d.proxies;await motherList.load();await proxyList.load();await motherTools.value?.load()}
 catch(e){error.value=(e as Error).message}finally{busy.value=false}
}
async function saveSettings(){
 busy.value=true;error.value="";
 try{settings.value.recovery.verification_http_statuses=parseVerificationStatuses(verificationStatuses.value);await api("/admin/refund/settings",{method:"PUT",body:JSON.stringify({settings:{...settings.value,child_proxy_recovery_success_count:settings.value.child_proxy_recovery_success_count===''?null:settings.value.child_proxy_recovery_success_count}})});emit("toast","执行设置已保存");settingsRevision.value++;await load()}
 catch(e){error.value=(e as Error).message}finally{busy.value=false}
}
async function editMother(row?:Mother){
 const request=++credentialRequest;loadingCredential.value=false;
 savedCredential.value="";savedRefresh.value="";
 mother.value=row?{...row,access_token:"",session:"",refresh_token:""}:{id:"",name:"",email:"",team_id:"",proxy_id:motherProxies.value.find(p=>p.enabled)?.id||"",seat_type:"default",enabled:true,has_secret:false,access_token:"",session:"",refresh_token:""};
 if(!motherProxies.value.some(p=>p.id===mother.value.proxy_id))mother.value.proxy_id="";
 motherPreview.value=undefined;motherCheck.value=undefined;
 formError.value="";modal.value="mother";
 if(row){loadingCredential.value=true;const id=row.id;try{const v=await api<{session:string;access_token:string;refresh_token:string}>(`/admin/refund/mother-management/${id}/credentials`);if(request===credentialRequest&&modal.value==='mother'&&mother.value.id===id){savedCredential.value=v.session||v.access_token;savedRefresh.value=v.refresh_token||'';mother.value.session=savedCredential.value;mother.value.refresh_token=savedRefresh.value}}catch(e){if(request===credentialRequest&&modal.value==='mother'&&mother.value.id===id)formError.value=(e as Error).message}finally{if(request===credentialRequest)loadingCredential.value=false}}
}
function editProxy(row?:ProxyRow){proxyCheck.value=undefined;proxy.value=row?{target:"both",...row,url:""}:{id:"",name:"",url:"",target:"both",enabled:true,has_secret:false};formError.value="";modal.value="proxy"}
function close(){credentialRequest++;loadingCredential.value=false;savedCredential.value="";savedRefresh.value="";modal.value="";motherCheck.value=undefined;proxyCheck.value=undefined;mother.value.access_token="";mother.value.session="";mother.value.refresh_token="";motherPreview.value=undefined;proxy.value.url="";removing.value=undefined}
async function parseMother(){
 const raw=mother.value.session?.trim()||"";
 if(!raw){formError.value="请粘贴 Session JSON 或 Access Token";return false}
 parsing.value=true;formError.value="";
 try{
  const preview=await api<CredentialPreview>("/admin/refund/mothers/preview",{method:"POST",body:JSON.stringify({session:raw})});
  if(raw!==mother.value.session?.trim())return false;
  motherPreview.value=preview;
  if(preview.email)mother.value.email=preview.email;
  if(preview.team_id)mother.value.team_id=preview.team_id;
  return true;
 }catch(e){formError.value=(e as Error).message;motherPreview.value=undefined;return false}finally{parsing.value=false}
}
async function importMother(event:Event){
 const input=event.target as HTMLInputElement,file=input.files?.[0];input.value="";
 if(!file)return;
 if(file.size>131072){formError.value="JSON 文件不能超过 128 KB";return}
 try{mother.value.session=await file.text();await parseMother()}catch{formError.value="读取 JSON 文件失败"}
}
async function checkMother(row?:Mother){
 busy.value=true;formError.value="";error.value="";
 try{
  if(!row&&mother.value.session?.trim()&&mother.value.session!==savedCredential.value&&!await parseMother())return;
  const result=await api<ResourceCheck>("/admin/refund/mothers/check",{method:"POST",body:JSON.stringify(row?{id:row.id}:motherInput())});
  if(row){row.check=result;await load()}else{motherCheck.value=result}
  if(result.valid)emit("toast",`${result.message} · ${result.latency_ms} ms`);
  else if(row)error.value=result.message;else formError.value=result.message;
 }catch(e){if(row)error.value=(e as Error).message;else formError.value=(e as Error).message}finally{busy.value=false}
}
async function checkProxy(row?:ProxyRow){
 busy.value=true;formError.value="";error.value="";
 try{
  const result=await api<ResourceCheck>("/admin/refund/proxies/check",{method:"POST",body:JSON.stringify(row?{id:row.id}:proxyInput())});
  if(row){row.check=result;if(result.valid)await load()}else{proxyCheck.value=result}
  if(result.valid)emit("toast",`${result.message} · ${result.latency_ms} ms`);
  else if(row)error.value=result.message;else formError.value=result.message;
 }catch(e){if(row)error.value=(e as Error).message;else formError.value=(e as Error).message}finally{busy.value=false}
}
async function save(){
 if(loadingCredential.value||parsing.value)return;
 busy.value=true;formError.value="";
 try{
  if(!removing.value&&modal.value==="mother"&&mother.value.session?.trim()&&mother.value.session!==savedCredential.value&&!await parseMother())return;
  if(removing.value){await api(`/admin/refund/${removing.value.kind}/${removing.value.id}`,{method:"DELETE"})}
  else{await api(`/admin/refund/${modal.value==="mother"?"mothers":"proxies"}`,{method:"POST",body:JSON.stringify(modal.value==="mother"?motherInput():proxyInput())})}
  close();await load();emit("toast","配置已更新");
 }catch(e){formError.value=(e as Error).message}finally{busy.value=false}
}
function openOrder(id:string){window.location.hash='/admin/orders?business=refund&order_id='+id}
let resourceTimer:ReturnType<typeof setInterval>|undefined,resourcesDisposed=false;
const resourceController=new AbortController();
onMounted(()=>{void load();resourceTimer=setInterval(async()=>{if(section.value!=='proxies'||document.visibilityState!=='visible'||busy.value||modal.value)return;try{await proxyList.load()}catch{}},15000)});
onUnmounted(()=>{resourcesDisposed=true;clearInterval(resourceTimer);resourceController.abort()});
</script>
<template>
 <div class="page-heading"><div><span class="eyebrow">REFUND OPERATIONS</span><h1>代退资源与调度</h1><p>管理代退母号与独立代理，设置并行、重试和异常恢复。</p></div><button class="btn secondary" :disabled="busy" @click="load">刷新状态</button></div>
 <div class="refund-resource-overview"><article class="panel"><span>母号资源</span><b>{{resourceSummary.mothers?.enabled||0}}<small> / {{resourceSummary.mothers?.total||0}} 已启用</small></b></article><article class="panel"><span>代退代理</span><b>{{resourceSummary.proxies?.enabled||0}}<small> / {{resourceSummary.proxies?.total||0}} 已启用</small></b></article><article class="panel"><span>执行队列</span><b class="queue-state">{{settings.enabled?'自动办理已开启':'自动办理已暂停'}}</b><small>{{runtimeReady?'执行环境已就绪':'执行环境暂未就绪'}}</small></article></div>
 <div class="tabs refund-resource-tabs" role="tablist" aria-label="代退资源分类"><button v-for="item in [{id:'mothers',label:'母号资源'},{id:'proxies',label:'代退代理'},{id:'schedule',label:'调度与恢复'}]" :key="item.id" role="tab" :aria-selected="section===item.id" :class="{active:section===item.id}" :aria-controls="'refund-resource-'+item.id" @click="section=item.id">{{item.label}}</button></div>
 <div class="execution-settings">
  <div v-if="error" class="error-banner" role="alert">{{error}}</div>
  <section v-show="section==='schedule'" id="refund-resource-schedule" role="tabpanel" class="panel">
   <div class="section-title"><div><h2>队列调度</h2><p>根据实际办理结果更新服务单并结算费用。</p></div></div>
   <form class="execution-form" @submit.prevent="saveSettings">
    <label class="checkbox-label"><input v-model="settings.query_personal_plan" type="checkbox"/>查询并记录办理前个人套餐</label>
    <p class="muted">开启后，在密码和 2FA 登录后查询个人套餐，再进入团队。关闭只跳过办理前套餐记录，异常后的套餐兜底仍按原配置执行。</p>
    <label class="checkbox-label"><input v-model="settings.enabled" type="checkbox"/>允许队列自动办理申请</label>
    <div class="form-row">
     <label>并行母号上限<input v-model.number="settings.max_parallel" type="number" min="1" max="8" required/></label>
     <label v-if="!settings.recovery.enabled">单次登录尝试次数上限<input v-model.number="settings.max_attempts" type="number" min="1" max="3" required/></label>
     <label>重试间隔（秒）<input v-model.number="settings.retry_seconds" type="number" min="5" max="300" required/></label>
     <label>登录后等待（秒）<input v-model.number="settings.resource_recovery.post_login_wait_seconds" type="number" min="0" max="60" step="1" required/><small>登录成功后等待，再接受邀请进入母号团队。默认 5 秒，0 表示不等待。</small></label>
     <label>同母号接单间隔（秒）<input v-model.number="settings.mother_interval_seconds" type="number" min="0" max="86400" step="1" required/></label>
    </div>
    <div class="child-proxy-policy"><div class="child-proxy-heading"><h3>子号代理</h3><span>主用优先 · 故障自动切换</span></div>
    <div class="child-proxy-fields"><label>主用代理<select v-model="settings.child_proxy_id" :required="settings.enabled"><option value="">请选择主用代理</option><option v-for="p in childProxies" :key="p.id" :value="p.id" :disabled="!p.enabled">{{p.name}}{{p.enabled?'':'（已停用）'}}</option></select></label><label>故障冷却（秒）<input v-model.number="settings.child_proxy_cooldown_seconds" type="number" min="30" max="3600" required/><small>冷却期间跳过故障代理。</small></label><label>恢复连接测试次数<input v-model.number="settings.child_proxy_recovery_success_count" type="number" min="1" max="20" placeholder="留空则冷却结束直接恢复"/><small>默认连续成功 3 次。任一次失败会重新冷却；留空则不测试。</small></label><label>恢复测试间隔（秒）<input v-model.number="settings.child_proxy_recovery_interval_seconds" type="number" min="1" max="300" required/><small>默认 5 秒，从上一次测试结束后计时。</small></label></div>
    <label>备用代理（可多选）</label><div class="child-proxy-options"><label v-for="p in childProxies.filter(p=>p.id!==settings.child_proxy_id)" :key="p.id" class="checkbox-label"><input type="checkbox" :checked="settings.child_proxy_backups.includes(p.id)" :disabled="!p.enabled" @change="selectBackup(p.id,($event.target as HTMLInputElement).checked)"/><span>{{p.name}}<small v-if="!p.enabled">已停用</small></span></label><p v-if="childProxies.filter(p=>p.id!==settings.child_proxy_id).length===0" class="muted">先在代退代理中添加子号可用的备用代理。</p></div>
    <ol v-if="settings.child_proxy_backups.length" class="child-proxy-priority"><li v-for="(id,index) in settings.child_proxy_backups" :key="id"><span>备用 {{index+1}} · {{proxies.find(p=>p.id===id)?.name||'已删除'}}</span><button type="button" class="btn secondary" :disabled="index===0" @click="moveBackup(id,-1)">上移</button><button type="button" class="btn secondary" :disabled="index===settings.child_proxy_backups.length-1" @click="moveBackup(id,1)">下移</button></li></ol>
    <p class="muted">网络或代理连接失败时，按备用顺序继续原步骤。主用正常时，新单仍优先主用；全部不可用时等待恢复。密码错误、账号异常不换代理，结果不明的请求继续核实。</p></div>
    <p class="muted">母号请求使用各自绑定的专属代理。子号只使用此处选择的代理，连接故障时按原重试规则切换，不直连。</p>
    <p class="muted">单个母号依次办理，不同母号可同时工作。暂停队列只阻止领取新申请，已经开始的任务继续运行；待核实申请按下方恢复和套餐兜底规则处理。</p>
    <p class="muted">接单间隔从同母号上一单成功完成后开始计算，默认 0 秒，不额外等待。保存后对正在等待的母号立即生效；到期后在下一轮调度（约 2 秒内）接单，其他空闲母号照常办理，已开始的任务及续跑不受影响。</p>
    <fieldset class="recovery-settings">
     <legend>母号冷却与残留清理</legend>
     <div class="form-row">
      <label>母号故障触发暂停次数<input v-model.number="settings.resource_recovery.failure_limit" type="number" min="1" max="10" required/></label>
      <label>母号故障冷却（秒）<input v-model.number="settings.resource_recovery.cooldown_seconds" type="number" min="30" max="3600" required/></label>
      <label>异常清理后观察（秒）<input v-model.number="settings.resource_recovery.watch_seconds" type="number" min="30" max="1800" required/></label>
      <label>清理重试间隔（秒）<input v-model.number="settings.resource_recovery.retry_seconds" type="number" min="5" max="300" required/></label>
      <label>转低频前失败次数<input v-model.number="settings.resource_recovery.fast_attempts" type="number" min="1" max="10" required/></label>
      <label>低频重试间隔（秒）<input v-model.number="settings.resource_recovery.slow_retry_seconds" type="number" min="60" max="3600" required/></label>
     </div>
     <p class="muted">满员未清理时停止分配；账号密码错误和邮箱验证不计入母号故障。订单结束后仍会检查残留，不重复收费。</p>
    </fieldset>
    <fieldset class="recovery-settings">
     <legend>账号停用后的再次提交</legend>
     <label>上游停用账号允许再次提交次数<input v-model.number="settings.deactivated_resubmit_limit" type="number" min="0" max="10" step="1" required/></label>
     <p class="muted">首次确认账号被上游停用后，还允许重新提交多少笔订单。0 表示不允许再次提交；设为 2 表示再允许 2 笔，第 3 笔拒绝。原单自动重试、同一请求重复发送不计数。</p>
    </fieldset>
    <fieldset class="recovery-settings">
     <legend>登录服务故障保护</legend>
     <div class="form-row">
      <label>触发保护的不同账号数<input v-model.number="settings.resource_recovery.login_failure_limit" type="number" min="2" max="20" required/></label>
      <label>故障统计窗口（秒）<input v-model.number="settings.resource_recovery.login_window_seconds" type="number" min="10" max="600" required/></label>
      <label>恢复探测间隔（秒）<input v-model.number="settings.resource_recovery.login_cooldown_seconds" type="number" min="5" max="3600" required/></label>
     </div>
     <p class="muted">多个账号在同一登录步骤遇到上游故障时暂停新登录，到期只放行一笔探测。密码错误、邮箱验证不触发；母号清理照常运行。</p>
    </fieldset>
    <fieldset class="recovery-settings">
     <legend>连续失败保护</legend>
     <div class="field-grid">
      <label>同母号团队会话失败次数<input v-model.number="settings.resource_recovery.team_session_failure_limit" type="number" min="0" max="10" required/><small>默认 3 次。无法进入目标团队时，达到次数或原重试次数用完后，先清理再换母号。0 关闭此项。</small></label>
      <label>母号自动停用的不同账号数<input v-model.number="settings.resource_recovery.mother_failed_accounts" type="number" min="0" max="20" required/><small>默认 2 个。连续出现母号故障或无法进入团队时停用；成功进入团队后清零。0 关闭此项。</small></label>
      <label>代退账号连续失败订单数<input v-model.number="settings.resource_recovery.account_failure_limit" type="number" min="0" max="20" required/><small>默认 4 笔，不限失败原因。按代退账号邮箱全站累计；单内重试不计，中间成功则清零。0 关闭此项。</small></label>
     </div>
     <p class="muted">母号停用后需手动启用；代退账号限制可在下方解除，不影响星桥用户账户。</p>
    </fieldset>
    <fieldset class="recovery-settings">
     <legend>母号异常自动换号</legend>
     <label class="checkbox-label"><input v-model="settings.mother_failover.enabled" type="checkbox"/>母号不可用或连续无法进入目标团队时，清理确认后换母号</label>
     <div class="form-row">
      <label>每笔申请最多换母号次数<input v-model.number="settings.mother_failover.max_switches" type="number" min="0" max="10" required/></label>
      <label>换母号前等待（秒）<input v-model.number="settings.mother_failover.retry_seconds" type="number" min="5" max="3600" required/></label>
     </div>
     <p class="muted">默认开启，最多换 3 次，每次等待 30 秒。按原申请提交时间排队，遵守并行上限和同母号接单间隔；其他母号忙碌时继续等待，不占并发、不重复冻结；可尝试的母号全部失效或换号次数用完时结束并释放费用。已邀请的申请先在原母号完成清理和复查，确认没有残留后才允许换号；提交结果未知时不换号重提。</p>
     <p class="muted">凭证过期或授权失效的母号不再接新单，更新凭证或校验通过后恢复。关闭此开关会暂停等待换号的申请。</p>
    </fieldset>
    <fieldset class="recovery-settings">
     <legend>异常恢复与占用告警</legend>
     <label class="checkbox-label"><input v-model="settings.recovery.enabled" type="checkbox"/>启用自动恢复与结果核验</label>
     <label>提交后转入结果核验的状态码（英文逗号分隔）<input v-model="verificationStatuses" placeholder="404" maxlength="180"/></label>
     <p class="muted">默认 404，可填写 404,409 等状态码；留空关闭额外匹配。仅用于最后提交阶段，命中后先核验实际结果，不直接判失败、不自动重新提交。自动恢复与套餐兜底开关控制后续核验。</p>
     <div class="form-row">
      <label>自动重试次数（首次失败之后）<input v-model.number="settings.recovery.max_retries" type="number" min="0" max="10" required/></label>
      <label>核实各轮间隔（秒，逗号分隔）<input v-model="verifyIntervals" required placeholder="30, 60, 120, 300"/></label>
      <label>清理各轮间隔（秒，逗号分隔）<input v-model="cleanupIntervals" required placeholder="15, 30, 60"/></label>
      <label>异常占用告警阈值（秒）<input v-model.number="settings.recovery.alert_seconds" type="number" min="60" max="86400" required/></label>
     </div>
     <label class="checkbox-label"><input v-model="settings.recovery.cleanup_failed" type="checkbox"/>明确未受理且重试结束后，自动清理本次资源并释放费用</label>
     <p class="muted">普通重试使用上方重试间隔；每轮核实与清理都从上一轮结束后计时。等待期间不占执行并发，同母号仍保持串行。关闭自动恢复不强行中断在途操作。</p>
     <p class="muted">关闭套餐兜底时，结果未知只做只读核实。开启后按下方规则移出、登录并核验；查询失败不视为 Free。超过轮次或告警阈值后，请到订单中心的代退页跟进。</p>
    </fieldset>
    <fieldset v-if="settings.recovery.plan_fallback" class="recovery-settings">
     <legend>停用待核实：移出后套餐兜底</legend>
     <label class="checkbox-label"><input v-model="settings.recovery.plan_fallback.enabled" type="checkbox"/>自动移出、重新登录并核验个人套餐</label>
     <div class="form-row">
      <label>最多重新办理轮次<input v-model.number="settings.recovery.plan_fallback.max_cycles" type="number" min="1" max="5" required/></label>
      <label>每轮核验尝试上限<input v-model.number="settings.recovery.plan_fallback.max_attempts" type="number" min="1" max="10" required/></label>
      <label>兜底登录后核验等待（秒）<input v-model.number="settings.resource_recovery.fallback_plan_wait_seconds" type="number" min="0" max="60" step="1" required/><small>每次个人账号登录成功后等待，再查询套餐。默认 5 秒，0 表示不等待。</small></label>
      <label>兜底及再次办理间隔（秒）<input v-model.number="settings.recovery.plan_fallback.retry_seconds" type="number" min="5" max="3600" required/></label>
     </div>
     <p class="muted">默认开启，等待 30 秒后启动，最多 2 轮、每轮 3 次。确认移出后重新登录；个人套餐为 Free 时按业务规则完成服务并结算，仍为付费套餐时保留原单重新邀请办理。未知套餐、登录失败和查询失败仅重试核验，不视为成功；Free 不代表退款到账。关闭自动恢复会暂停尚未开始的兜底。</p>
     <p class="muted">命中上方结果核验状态码的申请，只按每轮核验上限与间隔检查，不重新邀请办理。确认移出后释放母号供后续申请使用，费用继续冻结；超过核验上限转待跟进，确认 Free 后只结算一次。</p>
    </fieldset>
    <button class="btn primary" :disabled="busy">应用调度配置</button>
   </form>
   <RefundAccountRestrictions :active="section==='schedule'" :revision="settingsRevision"/>
   <RefundResourceRecovery :active="section==='schedule'"/>
  </section>
  <section v-show="section==='mothers'" id="refund-resource-mothers" role="tabpanel" class="panel">
   <RefundMotherTools ref="motherTools" :initial-tab="managedTab" :selected="managedMother" @close="managedMother=null" @snapshot="motherSnapshots=$event" @changed="motherList.load"/>
   <div class="section-title"><div><h2>母号管理</h2><p>在此维护母号与对应资源，申请人只需提交自己的账号资料。</p></div><button class="btn primary" :disabled="busy" @click="editMother()">录入母号</button></div>
   <div class="table-toolbar"><input v-model="motherQuery" type="search" placeholder="搜索资源名称或账号" maxlength="200"/><select v-model="motherStatus"><option value="">全部状态</option><option value="enabled">启用</option><option value="disabled">停用</option><option value="busy">使用中</option><option value="idle">空闲</option></select></div><p v-if="motherError" class="alert danger">{{motherError}}</p><div class="table-scroll mother-resource-scroll"><table class="mother-resource-table"><thead><tr>
     <SortHeader v-for="[field,label] in [['name','名称 / 邮箱'],['enabled','接单状态']]" :key="field" :field="field" :label="label" :sort-by="motherSort" :sort-dir="motherDir" @sort="motherList.sort"/><th>套餐</th><SortHeader label="套餐到期" field="plan_expires_at" :sort-by="motherSort" :sort-dir="motherDir" @sort="motherList.sort"/><th>当前 5x 参考价</th><th>团队</th><th>专属代理</th>
     <SortHeader v-for="[field,label] in [['member_count','当前成员'],['standard_remaining','普通席位'],['premium_remaining','高级席位 5x'],['held','临停席位'],['handled','累计办理'],['settled','累计服务费'],['token_expires_at','凭据续期']]" :key="field" :field="field" :label="label" :sort-by="motherSort" :sort-dir="motherDir" @sort="motherList.sort"/><th>校验</th><th>操作</th>
    </tr></thead><tbody>
     <tr v-for="m in (motherRows as Mother[])" :key="m.id">
      <td class="mother-identity"><strong>{{m.name}}</strong><small class="block">{{m.email}}</small><button v-if="m.active_refund" class="link" @click="openOrder(m.active_refund)">{{m.active_email}} · {{statuses[m.active_status||'']||'处理中'}}</button></td>
      <td><button class="mother-switch" role="switch" :aria-checked="m.enabled" :aria-label="m.name+' 接单状态'" :disabled="busy" @click="changeMother(m,{enabled:!m.enabled})"><i :class="{on:m.enabled}"/>{{m.enabled?'已启用':'已停用'}}</button><small v-if="m.auto_disabled_reason" class="block form-error">{{m.auto_disabled_reason}}<br/>{{m.auto_disabled_at?displayTime(m.auto_disabled_at):''}}</small><small v-if="m.auth_blocked" class="block form-error">授权失效，暂停接单</small><small v-else-if="m.token_expires_at&amp;&amp;new Date(m.token_expires_at).getTime()&lt;=Date.now()" class="block form-error">凭据已过期</small></td>
      <td>{{snapshot(m).plan?.subscription_plan||m.plan_type||'—'}}<small class="block">{{m.seat_type==='prolite'?'高级席位':'普通席位'}}</small></td>
      <td><strong>{{snapshot(m).plan?.expires_at?displayTime(snapshot(m).plan.expires_at):'—'}}</strong><small class="block">{{snapshot(m).plan?.checked_at?'查询于 '+displayTime(snapshot(m).plan.checked_at):'未查询'}}</small></td>
      <td><strong>{{seatPrice(m)}}</strong><small class="block">125 / 30 × 剩余天数</small></td>
      <td class="mother-team" :title="m.team_id">{{m.team_id}}</td>
      <td><select class="mother-proxy-select" :value="m.proxy_id" :aria-label="m.name+' 专属代理'" :disabled="busy" @change="changeMother(m,{proxy_id:($event.target as HTMLSelectElement).value})"><option v-if="!motherProxies.some(p=>p.id===m.proxy_id)" :value="m.proxy_id">专属代理不可用</option><option v-for="p in motherProxies" :key="p.id" :value="p.id" :disabled="!p.enabled">{{p.name}}{{p.enabled?'':'（已停用）'}}</option></select></td>
      <td><strong>{{snapshot(m).members?.count??'—'}}</strong><small class="block">{{snapshot(m).members?'个子号 · 上次查询':'未查询'}}</small><button class="btn secondary" @click="manage(m,'members')">查看成员</button></td>
      <td v-for="key in ['standard','premium']" :key="key"><strong>{{seats(m,key)?seats(m,key).remaining+' / '+seats(m,key).total:'— / —'}}</strong><small class="block">剩余 / 总量</small></td>
      <td><strong>{{seats(m,'standard')||seats(m,'premium')?'普通 '+(seats(m,'standard')?.held??'—')+' / 5x '+(seats(m,'premium')?.held??'—'):'—'}}</strong><small class="block">临停</small></td>
      <td><strong>{{motherSnapshots[m.id]?.handled||0}} 笔</strong><small class="block">已完成 {{motherSnapshots[m.id]?.completed||0}} 笔</small></td>
      <td><strong>{{cash(motherSnapshots[m.id]?.settled)}} 积分</strong><small class="block">已结算</small></td>
      <td><span class="badge" :class="m.has_refresh?'green':'gray'">{{m.has_refresh?'已保存 RT':'无 RT'}}</span><small class="block">AT {{m.token_expires_at?displayTime(m.token_expires_at):'未提供到期时间'}}</small></td>
      <td><ResourceCheckSummary :check="m.check"/></td>
      <td><div class="mother-row-actions"><button class="link" :disabled="busy" @click="checkMother(m)">校验 / 刷新席位</button><button class="link" :disabled="busy" @click="manage(m)">套餐 / 成员 / 凭据</button><button class="link" :disabled="busy" @click="editMother(m)">编辑</button><button class="link danger" :disabled="busy" @click="removing={kind:'mothers',id:m.id,name:m.name};modal='delete';formError=''">删除</button></div></td>
     </tr>
     <tr v-if="!motherRows.length"><td colspan="16" class="empty-state">{{motherLoading?'正在加载…':motherQuery||motherStatus?'没有符合条件的母号':'请先录入网络出口，再录入母号资源'}}</td></tr>
    </tbody></table></div>
  <ListPager v-model:page="motherPage" v-model:size="motherSize" :total="motherData.total||0" :loading="motherLoading"/></section>
  <section v-show="section==='proxies'" id="refund-resource-proxies" role="tabpanel" class="panel">
   <div class="section-title"><div><h2>代退代理</h2><p>连接信息加密保管，列表只展示名称与可用状态。</p></div><button class="btn primary" :disabled="busy" @click="editProxy()">录入代理</button></div>
   <div class="table-toolbar"><input v-model="proxyQuery" type="search" placeholder="搜索资源名称或账号" maxlength="200"/><select v-model="proxyStatus"><option value="">全部状态</option><option value="enabled">启用</option><option value="disabled">停用</option><option value="cooling">冷却中</option></select><select v-model="proxyTarget"><option value="">全部适用对象</option><option value="mother">母号可用</option><option value="child">子号可用</option></select></div><p v-if="proxyError" class="alert danger">{{proxyError}}</p><div class="table-scroll"><table><thead><tr><SortHeader label="名称" field="name" :sort-by="proxySort" :sort-dir="proxyDir" @sort="proxyList.sort"/><SortHeader label="适用对象" field="target" :sort-by="proxySort" :sort-dir="proxyDir" @sort="proxyList.sort"/><SortHeader label="状态" field="enabled" :sort-by="proxySort" :sort-dir="proxyDir" @sort="proxyList.sort"/><th>配置</th><SortHeader label="冷却释放时间" field="failure_until" :sort-by="proxySort" :sort-dir="proxyDir" @sort="proxyList.sort"/><th>连接测试</th><th>操作</th></tr></thead><tbody>
    <tr v-for="p in (proxyRows as ProxyRow[])" :key="p.id"><td>{{p.name}}</td><td>{{targetName(p)}}</td><td><span class="badge" :class="p.enabled?'green':'gray'">{{p.enabled?'启用':'停用'}}</span></td><td>{{settings.child_proxy_id===p.id?'子号主用':settings.child_proxy_backups.includes(p.id)?'子号备用 '+(settings.child_proxy_backups.indexOf(p.id)+1):'连接信息已保存'}}</td><td><ProxyCooldown :until="p.failure_until" :failures="p.failure_until?1:0" :probe-until="p.probe_until" :successes="p.recovery_successes" :required="p.recovery_required"/></td><td><ResourceCheckSummary :check="p.check"/></td><td><div class="execution-actions"><button class="btn secondary" :disabled="busy" @click="checkProxy(p)">测试代理</button><button class="btn secondary" :disabled="busy" @click="editProxy(p)">编辑</button><button class="btn secondary danger" :disabled="busy" @click="removing={kind:'proxies',id:p.id,name:p.name};modal='delete';formError=''">删除</button></div></td></tr>
    <tr v-if="!proxyRows.length"><td colspan="7" class="empty-state">还没有网络出口配置。</td></tr>
   </tbody></table></div>
  <ListPager v-model:page="proxyPage" v-model:size="proxySize" :total="proxyData.total||0" :loading="proxyLoading"/></section>
 </div>
 <Modal v-if="modal" :title="modal==='mother'?'母号配置':modal==='proxy'?'代理配置':'删除配置'" :busy="busy||loadingCredential||parsing" @close="!busy&&!loadingCredential&&close()">
  <form class="execution-form" @submit.prevent="save">
   <template v-if="modal==='mother'">
    <label>母号名称<input v-model="mother.name" required maxlength="100"/></label>
    <p v-if="loadingCredential" class="muted" role="status">正在读取已保存凭据…</p>
    <label>Session JSON / Access Token<textarea v-model="mother.session" rows="5" autocomplete="off" spellcheck="false" :disabled="busy||parsing||loadingCredential" :required="!mother.id" :placeholder="mother.id?'留空保留已保存凭据':'粘贴 Session JSON 或母号 AT，自动读取账号信息'"/></label>
    <input ref="fileInput" hidden type="file" accept=".json,application/json" @change="importMother"/>
    <div class="execution-actions"><button type="button" class="btn secondary" :disabled="busy||parsing||loadingCredential" @click="fileInput?.click()">导入 JSON</button><button type="button" class="btn secondary" :disabled="busy||parsing||loadingCredential" @click="parseMother">{{parsing?'解析中…':'解析凭据'}}</button><button type="button" class="btn secondary" :disabled="busy||parsing||loadingCredential" @click="checkMother()">校验 / 刷新席位</button></div>
    <ResourceCheckSummary v-if="motherCheck" :check="motherCheck" mother/>
    <p v-if="motherPreview" class="muted">{{motherPreview.email||'未读取到邮箱'}} · {{motherPreview.plan_type||'未提供计划'}}<br/>{{motherPreview.expires_at?`AT 到期：${displayTime(motherPreview.expires_at)}`:'AT 未提供到期时间'}}{{motherPreview.has_refresh?' · 已读取 RT':''}}。解析仅提取资料，校验用于确认可用性。</p>
    <label>母号邮箱<input v-model="mother.email" type="email" placeholder="解析后自动填写"/></label>
    <label>团队 ID<input v-model="mother.team_id" placeholder="自动读取，也可手工填写"/></label>
    <label>Refresh Token（可选）<input :disabled="loadingCredential" v-model="mother.refresh_token" type="password" autocomplete="new-password" :placeholder="mother.has_refresh?'已保存，留空保留':'可从 Session JSON 自动提取'"/></label>
    <label>母号专属代理<select v-model="mother.proxy_id" required><option value="" disabled>请选择母号专属代理</option><option v-for="p in motherProxies" :key="p.id" :value="p.id">{{p.name}}{{p.enabled?'':'（已停用）'}}</option></select></label>
    <p class="muted">母号的校验和业务请求均使用此专属代理，子号使用上方的全局代理。凭据加密保存，编辑时显示已保存的 Session；原记录只有 AT 时显示 AT。</p>
    <label>席位类型<select v-model="mother.seat_type"><option value="default">标准席位（default）</option><option value="prolite">高级席位（prolite）</option></select></label>
    <label class="checkbox-label"><input v-model="mother.enabled" type="checkbox"/>启用母号</label>
   </template>
   <template v-else-if="modal==='proxy'">
    <label>代理名称<input v-model="proxy.name" required maxlength="100"/></label>
    <label>适用对象<select v-model="proxy.target"><option value="both">母号 / 子号通用</option><option value="mother">仅母号</option><option value="child">仅子号</option></select></label>
    <label>代理地址<input v-model="proxy.url" type="password" autocomplete="new-password" :required="!proxy.id" :placeholder="proxy.id?'留空保留原地址':'IP:端口:用户名:密码'"/></label>
    <p class="muted">支持 IP:端口:用户名:密码，默认 HTTP；也支持完整 HTTP、HTTPS、SOCKS5、SOCKS5H 地址。使用固定出口，任务运行中不能修改地址。</p>
    <button type="button" class="btn secondary" :disabled="busy" @click="checkProxy()">测试代理</button>
    <ResourceCheckSummary v-if="proxyCheck" :check="proxyCheck"/>
    <label class="checkbox-label"><input v-model="proxy.enabled" type="checkbox"/>启用代理</label>
   </template>
   <p v-else-if="removing?.kind==='mothers'">确认删除“{{removing.name}}”？删除后不再分配新申请，历史记录保留，已开始的申请继续收尾。</p>
   <p v-else>确认删除“{{removing?.name}}”？删除后不再用于新申请，已开始的申请继续收尾。</p>
   <p v-if="formError" class="form-error" role="alert">{{formError}}</p>
   <footer class="modal-actions"><button type="button" class="btn secondary" :disabled="busy||parsing||loadingCredential" @click="close">取消</button><button class="btn primary" :disabled="busy||parsing||loadingCredential">{{busy?'处理中…':modal==='delete'?'确认删除':'保存'}}</button></footer>
  </form>
 </Modal>
 
</template>

<style scoped>
.refund-resource-overview{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;margin-bottom:28px}.refund-resource-overview article{padding:22px}.refund-resource-overview span{display:block;font-size:11px;color:var(--muted);margin-bottom:15px}.refund-resource-overview b{display:block;font-size:28px}.refund-resource-overview small{font-size:11px;font-weight:400;color:var(--muted)}.refund-resource-overview b.queue-state{font-size:16px;margin-bottom:10px}.refund-resource-tabs{margin-bottom:22px}.refund-resource-tabs button{padding:13px 22px}@media(max-width:650px){.refund-resource-overview{grid-template-columns:1fr 1fr;gap:10px}.refund-resource-overview article{padding:17px}.refund-resource-overview article:last-child{grid-column:1/-1}.refund-resource-tabs button{padding:12px 16px}}

.mother-resource-scroll{overflow:auto;max-height:680px}.execution-settings .mother-resource-table{min-width:2200px;table-layout:auto}.mother-resource-table th{white-space:nowrap;position:sticky;top:0;z-index:1;background:var(--surface)}.mother-resource-table td{padding:18px 14px;font-size:12px}.mother-resource-table small{font-size:11px;color:var(--muted);margin-top:7px}.mother-identity{min-width:190px}.mother-team{max-width:130px;overflow-wrap:anywhere;font-family:monospace}.mother-proxy-select{min-width:145px}.mother-row-actions{display:grid;gap:10px;white-space:nowrap}.mother-row-actions .link{text-align:left}.mother-switch{display:flex;align-items:center;gap:7px;background:none;border:0;white-space:nowrap;color:var(--text);font-size:12px}.mother-switch i{display:block;width:26px;height:15px;border-radius:12px;background:#b9c3c6;position:relative}.mother-switch i:after{content:'';position:absolute;top:3px;left:3px;width:9px;height:9px;border-radius:50%;background:white}.mother-switch i.on{background:#0f8b78}.mother-switch i.on:after{left:14px}.execution-settings{display:grid;gap:22px}.panel{padding:24px;min-width:0;overflow:auto}.section-title,.execution-actions{display:flex;justify-content:space-between;gap:14px;align-items:center;flex-wrap:wrap}.section-title p{color:var(--muted);font-size:12px;margin:8px 0 20px}.execution-form{display:grid;gap:16px}.execution-form label{display:grid;gap:8px;font-size:12px}.execution-form .checkbox-label{display:flex;align-items:center;gap:10px}.checkbox-label input{width:auto}.form-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px}.muted{font-size:12px;line-height:1.8;margin:0}.recovery-settings{border:1px solid var(--line);border-radius:12px;padding:20px;display:grid;gap:16px}.recovery-settings legend{padding:0 8px;font-weight:600}.error-banner,.form-error{color:#b4423c;padding:10px}.execution-form .btn{justify-self:start}.execution-settings table{width:100%;min-width:700px}.execution-settings td{vertical-align:top}.execution-actions .btn{padding:7px 12px}.modal-actions{display:flex;justify-content:flex-end;gap:12px}.execution-form input,.execution-form select,.execution-form textarea{max-width:100%;min-width:0} @media(max-width:600px){.panel{padding:16px}.recovery-settings{padding:14px}}
</style>
<style scoped>
.child-proxy-policy{border:1px solid #dce7e9;border-radius:12px;padding:20px;margin:8px 0 16px}.child-proxy-heading{display:flex;justify-content:space-between;gap:15px;align-items:center;margin-bottom:16px}.child-proxy-heading h3{font-size:15px;margin:0}.child-proxy-heading>span{font-size:11px;color:#488579}.child-proxy-fields{display:grid;grid-template-columns:2fr 1fr;gap:18px}.child-proxy-fields small{font-size:11px;font-weight:normal;color:#81929a}.child-proxy-options{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0}.child-proxy-options label{display:flex;align-items:center;background:#f7f9fa;border:1px solid #dce7e9;border-radius:8px;padding:11px 15px;gap:9px}.child-proxy-options small{display:block;font-size:11px;color:#a16a68}.child-proxy-priority{list-style:none;padding:0;display:grid;gap:8px}.child-proxy-priority li{display:flex;gap:8px;align-items:center;font-size:12px}.child-proxy-priority li>span{flex:1}.child-proxy-priority button{font-size:11px;padding:5px 9px}.child-proxy-policy>p{margin-bottom:0;line-height:1.8}@media(max-width:600px){.child-proxy-fields{grid-template-columns:1fr}.child-proxy-heading{align-items:flex-start;flex-direction:column;gap:6px}.child-proxy-policy{padding:14px}.child-proxy-options label{width:100%}}
</style>
