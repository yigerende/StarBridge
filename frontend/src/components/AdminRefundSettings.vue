<script setup lang="ts">
import ProxyCooldown from "./ProxyCooldown.vue";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import {api as bridgeAPI,labels as statuses} from '../lib'
async function api<T=any>(path:string,options?:{method?:string;body?:string}):Promise<T>{return bridgeAPI(path,options?.method||'GET',options?.body?JSON.parse(options.body):undefined) as Promise<T>}
import ResourceCheckSummary from "../refund/ResourceCheckSummary.vue";
import type { ResourceCheck } from "../refund/resource-check";
import Modal from "../refund/Modal.vue";

import { parseVerificationStatuses } from "../refund/admin-verification-statuses";
type ProxyRow = { target?:string; failure_until?:string; failure_code?:string; check?:ResourceCheck; id:string; name:string; enabled:boolean; has_secret:boolean; url?:string };
type Mother = { auth_blocked?:boolean; check?:ResourceCheck; id:string; name:string; email:string; team_id:string; proxy_id:string; seat_type:string; enabled:boolean; has_secret:boolean; access_token?:string; session?:string; refresh_token?:string; has_refresh?:boolean; plan_type?:string; token_expires_at?:string; active_refund?:string; active_email?:string; active_status?:string };
type CredentialPreview = {email:string;team_id:string;plan_type:string;expires_at:string|null;has_refresh:boolean};
type Recovery={verification_http_statuses?:number[];plan_fallback?:{enabled:boolean;max_cycles:number;max_attempts:number;retry_seconds:number};enabled:boolean;max_retries:number;verify_intervals:number[];cleanup_intervals:number[];alert_seconds:number;cleanup_failed:boolean};
type Settings = { child_proxy_backups:string[];child_proxy_cooldown_seconds:number; mother_failover:{enabled:boolean;max_switches:number;retry_seconds:number}; recovery:Recovery; enabled:boolean; max_parallel:number; max_attempts:number; retry_seconds:number; child_proxy_id:string; mother_interval_seconds:number };
const section=ref('mothers'),runtimeReady=ref(false);
const emit=defineEmits<{toast:[message:string]}>();
const settings=ref<Settings>({child_proxy_backups:[],child_proxy_cooldown_seconds:300,mother_failover:{enabled:true,max_switches:3,retry_seconds:30},recovery:{plan_fallback:{enabled:true,max_cycles:2,max_attempts:3,retry_seconds:30},enabled:true,max_retries:3,verify_intervals:[30,60,120,300],cleanup_intervals:[15,30,60],alert_seconds:600,cleanup_failed:true},enabled:false,max_parallel:2,max_attempts:2,retry_seconds:30,child_proxy_id:"",mother_interval_seconds:0});
const intervalText=(key:'verify_intervals'|'cleanup_intervals')=>computed({get:()=>settings.value.recovery[key].join(', '),set:(value:string)=>{settings.value.recovery[key]=value.split(/[,，\s]+/).filter(Boolean).map(Number)}});
const verifyIntervals=intervalText('verify_intervals'),cleanupIntervals=intervalText('cleanup_intervals');
const verificationStatuses=ref("404");
const mothers=ref<Mother[]>([]),proxies=ref<ProxyRow[]>([]),busy=ref(false),error=ref(""),formError=ref(""),modal=ref("");
const mother=ref<Mother>({id:"",name:"",email:"",team_id:"",proxy_id:"",seat_type:"default",enabled:true,has_secret:false,access_token:""});
const proxy=ref<ProxyRow>({id:"",name:"",url:"",target:"both",enabled:true,has_secret:false});
const motherProxies=computed(()=>proxies.value.filter(p=>p.target!=='child'));
const childProxies=computed(()=>proxies.value.filter(p=>p.target!=='mother'));
const targetName=(p:ProxyRow)=>p.target==='mother'?'母号':p.target==='child'?'子号':'母号 / 子号';
function selectBackup(id:string,checked:boolean){settings.value.child_proxy_backups=checked?[...settings.value.child_proxy_backups,id]:settings.value.child_proxy_backups.filter(x=>x!==id)}
function moveBackup(id:string,by:number){const a=[...settings.value.child_proxy_backups],i=a.indexOf(id),to=i+by;if(to>=0&&to<a.length){[a[i],a[to]]=[a[to],a[i]];settings.value.child_proxy_backups=a}}
watch(()=>settings.value.child_proxy_id,id=>{settings.value.child_proxy_backups=settings.value.child_proxy_backups.filter(x=>x!==id)});
const removing=ref<{kind:string;id:string;name:string}>();
const motherCheck=ref<ResourceCheck>(),proxyCheck=ref<ResourceCheck>();
const motherPreview=ref<CredentialPreview>(),parsing=ref(false),fileInput=ref<HTMLInputElement>();
watch(()=>mother.value.session,()=>{motherPreview.value=undefined});
watch(()=>[mother.value.session,mother.value.access_token,mother.value.team_id,mother.value.email,mother.value.proxy_id],()=>{motherCheck.value=undefined});
watch(()=>proxy.value.url,()=>{proxyCheck.value=undefined});
const motherInput=()=>{const {check,...value}=mother.value;return value};
const proxyInput=()=>{const {check,...value}=proxy.value;return value};
const displayTime=(value:string)=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
async function load(){
 busy.value=true;error.value="";
 try{const d=await api<{settings:Settings;mothers:Mother[];proxies:ProxyRow[];runtime_ready:boolean}>("/admin/refund/settings");runtimeReady.value=d.runtime_ready;settings.value={...d.settings,child_proxy_backups:d.settings.child_proxy_backups||[],child_proxy_cooldown_seconds:d.settings.child_proxy_cooldown_seconds||300};settings.value.recovery.plan_fallback??={enabled:true,max_cycles:2,max_attempts:3,retry_seconds:30};verificationStatuses.value=(settings.value.recovery.verification_http_statuses??[404]).join(',');mothers.value=d.mothers;proxies.value=d.proxies}
 catch(e){error.value=(e as Error).message}finally{busy.value=false}
}
async function saveSettings(){
 busy.value=true;error.value="";
 try{settings.value.recovery.verification_http_statuses=parseVerificationStatuses(verificationStatuses.value);await api("/admin/refund/settings",{method:"PUT",body:JSON.stringify({settings:settings.value})});emit("toast","执行设置已保存");await load()}
 catch(e){error.value=(e as Error).message}finally{busy.value=false}
}
function editMother(row?:Mother){
 mother.value=row?{...row,access_token:"",session:"",refresh_token:""}:{id:"",name:"",email:"",team_id:"",proxy_id:motherProxies.value.find(p=>p.enabled)?.id||"",seat_type:"default",enabled:true,has_secret:false,access_token:"",session:"",refresh_token:""};
 if(!motherProxies.value.some(p=>p.id===mother.value.proxy_id))mother.value.proxy_id="";
 motherPreview.value=undefined;motherCheck.value=undefined;
 formError.value="";modal.value="mother";
}
function editProxy(row?:ProxyRow){proxyCheck.value=undefined;proxy.value=row?{target:"both",...row,url:""}:{id:"",name:"",url:"",target:"both",enabled:true,has_secret:false};formError.value="";modal.value="proxy"}
function close(){modal.value="";motherCheck.value=undefined;proxyCheck.value=undefined;mother.value.access_token="";mother.value.session="";mother.value.refresh_token="";motherPreview.value=undefined;proxy.value.url="";removing.value=undefined}
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
  if(!row&&mother.value.session?.trim()&&!await parseMother())return;
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
 busy.value=true;formError.value="";
 try{
  if(!removing.value&&modal.value==="mother"&&mother.value.session?.trim()&&!await parseMother())return;
  if(removing.value){await api(`/admin/refund/${removing.value.kind}/${removing.value.id}`,{method:"DELETE"})}
  else{await api(`/admin/refund/${modal.value==="mother"?"mothers":"proxies"}`,{method:"POST",body:JSON.stringify(modal.value==="mother"?motherInput():proxyInput())})}
  close();await load();emit("toast","配置已更新");
 }catch(e){formError.value=(e as Error).message}finally{busy.value=false}
}
function openOrder(id:string){window.location.hash='/admin/orders?business=refund&order_id='+id}
let resourceTimer:ReturnType<typeof setInterval>|undefined,resourcesDisposed=false;
const resourceController=new AbortController();
onMounted(()=>{void load();resourceTimer=setInterval(async()=>{if(section.value!=='proxies'||document.visibilityState!=='visible'||busy.value||modal.value)return;try{const d=await bridgeAPI("/admin/refund/settings","GET",undefined,{},AbortSignal.any([resourceController.signal,AbortSignal.timeout(12000)]));if(!resourcesDisposed&&!busy.value&&!modal.value)proxies.value=d.proxies}catch{}},15000)});
onUnmounted(()=>{resourcesDisposed=true;clearInterval(resourceTimer);resourceController.abort()});
</script>
<template>
 <div class="page-heading"><div><span class="eyebrow">REFUND OPERATIONS</span><h1>代退资源与调度</h1><p>管理代退母号与独立代理，设置并行、重试和异常恢复。</p></div><button class="btn secondary" :disabled="busy" @click="load">刷新状态</button></div>
 <div class="refund-resource-overview"><article class="panel"><span>母号资源</span><b>{{mothers.filter(m=>m.enabled).length}}<small> / {{mothers.length}} 已启用</small></b></article><article class="panel"><span>代退代理</span><b>{{proxies.filter(p=>p.enabled).length}}<small> / {{proxies.length}} 已启用</small></b></article><article class="panel"><span>执行队列</span><b class="queue-state">{{settings.enabled?'自动办理已开启':'自动办理已暂停'}}</b><small>{{runtimeReady?'执行环境已就绪':'执行环境暂未就绪'}}</small></article></div>
 <div class="tabs refund-resource-tabs" role="tablist" aria-label="代退资源分类"><button v-for="item in [{id:'mothers',label:'母号资源'},{id:'proxies',label:'代退代理'},{id:'schedule',label:'调度与恢复'}]" :key="item.id" role="tab" :aria-selected="section===item.id" :class="{active:section===item.id}" :aria-controls="'refund-resource-'+item.id" @click="section=item.id">{{item.label}}</button></div>
 <div class="execution-settings">
  <div v-if="error" class="error-banner" role="alert">{{error}}</div>
  <section v-show="section==='schedule'" id="refund-resource-schedule" role="tabpanel" class="panel">
   <div class="section-title"><div><h2>队列调度</h2><p>根据实际办理结果更新服务单并结算费用。</p></div></div>
   <form class="execution-form" @submit.prevent="saveSettings">
    <label class="checkbox-label"><input v-model="settings.enabled" type="checkbox"/>允许队列自动办理申请</label>
    <div class="form-row">
     <label>并行母号上限<input v-model.number="settings.max_parallel" type="number" min="1" max="8" required/></label>
     <label v-if="!settings.recovery.enabled">单次登录尝试次数上限<input v-model.number="settings.max_attempts" type="number" min="1" max="3" required/></label>
     <label>重试间隔（秒）<input v-model.number="settings.retry_seconds" type="number" min="5" max="300" required/></label>
     <label>同母号接单间隔（秒）<input v-model.number="settings.mother_interval_seconds" type="number" min="0" max="86400" step="1" required/></label>
    </div>
    <div class="child-proxy-policy"><div class="child-proxy-heading"><h3>子号代理</h3><span>主用优先 · 故障自动切换</span></div>
    <div class="child-proxy-fields"><label>主用代理<select v-model="settings.child_proxy_id" :required="settings.enabled"><option value="">请选择主用代理</option><option v-for="p in childProxies" :key="p.id" :value="p.id" :disabled="!p.enabled">{{p.name}}{{p.enabled?'':'（已停用）'}}</option></select></label><label>故障冷却（秒）<input v-model.number="settings.child_proxy_cooldown_seconds" type="number" min="30" max="3600" required/><small>冷却期间跳过故障代理，到期后先尝试一笔。</small></label></div>
    <label>备用代理（可多选）</label><div class="child-proxy-options"><label v-for="p in childProxies.filter(p=>p.id!==settings.child_proxy_id)" :key="p.id" class="checkbox-label"><input type="checkbox" :checked="settings.child_proxy_backups.includes(p.id)" :disabled="!p.enabled" @change="selectBackup(p.id,($event.target as HTMLInputElement).checked)"/><span>{{p.name}}<small v-if="!p.enabled">已停用</small></span></label><p v-if="childProxies.filter(p=>p.id!==settings.child_proxy_id).length===0" class="muted">先在代退代理中添加子号可用的备用代理。</p></div>
    <ol v-if="settings.child_proxy_backups.length" class="child-proxy-priority"><li v-for="(id,index) in settings.child_proxy_backups" :key="id"><span>备用 {{index+1}} · {{proxies.find(p=>p.id===id)?.name||'已删除'}}</span><button type="button" class="btn secondary" :disabled="index===0" @click="moveBackup(id,-1)">上移</button><button type="button" class="btn secondary" :disabled="index===settings.child_proxy_backups.length-1" @click="moveBackup(id,1)">下移</button></li></ol>
    <p class="muted">网络或代理连接失败时，按备用顺序继续原步骤。主用正常时，新单仍优先主用；全部不可用时等待恢复。密码错误、账号异常不换代理，结果不明的请求继续核实。</p></div>
    <p class="muted">母号请求使用各自绑定的专属代理。子号只使用此处选择的代理，连接故障时按原重试规则切换，不直连。</p>
    <p class="muted">单个母号依次办理，不同母号可同时工作。暂停队列只阻止领取新申请，已经开始的任务继续运行；待核实申请按下方恢复和套餐兜底规则处理。</p>
    <p class="muted">接单间隔从同母号上一单成功完成后开始计算，默认 0 秒，不额外等待。保存后对正在等待的母号立即生效；到期后在下一轮调度（约 2 秒内）接单，其他空闲母号照常办理，已开始的任务及续跑不受影响。</p>
    <fieldset class="recovery-settings">
     <legend>母号异常自动换号</legend>
     <label class="checkbox-label"><input v-model="settings.mother_failover.enabled" type="checkbox"/>开始办理前母号不可用时，重新排队换母号</label>
     <div class="form-row">
      <label>每笔申请最多换母号次数<input v-model.number="settings.mother_failover.max_switches" type="number" min="0" max="10" required/></label>
      <label>换母号前等待（秒）<input v-model.number="settings.mother_failover.retry_seconds" type="number" min="5" max="3600" required/></label>
     </div>
     <p class="muted">默认开启，最多换 3 次，每次等待 30 秒。按原申请提交时间排队，遵守并行上限和同母号接单间隔；没有可用母号就继续等待，不占执行并发，也不重复冻结费用。已开始邀请或办理的申请保留原母号，避免重复操作。</p>
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
      <label>兜底及再次办理间隔（秒）<input v-model.number="settings.recovery.plan_fallback.retry_seconds" type="number" min="5" max="3600" required/></label>
     </div>
     <p class="muted">默认开启，等待 30 秒后启动，最多 2 轮、每轮 3 次。确认移出后重新登录；个人套餐为 Free 时按业务规则完成服务并结算，仍为付费套餐时保留原单重新邀请办理。未知套餐、登录失败和查询失败仅重试核验，不视为成功；Free 不代表退款到账。关闭自动恢复会暂停尚未开始的兜底。</p>
     <p class="muted">命中上方结果核验状态码的申请，只按每轮核验上限与间隔检查，不重新邀请办理。确认移出后释放母号供后续申请使用，费用继续冻结；超过核验上限转待跟进，确认 Free 后只结算一次。</p>
    </fieldset>
    <button class="btn primary" :disabled="busy">应用调度配置</button>
   </form>
  </section>
  <section v-show="section==='mothers'" id="refund-resource-mothers" role="tabpanel" class="panel">
   <div class="section-title"><div><h2>母号管理</h2><p>在此维护母号与对应资源，申请人只需提交自己的账号资料。</p></div><button class="btn primary" :disabled="busy" @click="editMother()">录入母号</button></div>
   <div class="table-scroll"><table><thead><tr><th>母号</th><th>团队 / 席位</th><th>代理</th><th>校验 / 席位（剩余 / 总数）</th><th>状态</th><th>当前申请</th><th>操作</th></tr></thead><tbody>
    <tr v-for="m in mothers" :key="m.id"><td><strong>{{m.name}}</strong><small class="block">{{m.email}}</small><small v-if="m.plan_type" class="block">{{m.plan_type}}</small></td><td>{{m.team_id}}<small class="block">{{m.seat_type==='default'?'标准席位':'高级席位（prolite）'}}</small><small v-if="m.token_expires_at" class="block">AT 到期：{{displayTime(m.token_expires_at)}}</small><small v-if="m.has_refresh" class="block">RT 已加密保存</small></td><td>{{proxies.find(p=>p.id===m.proxy_id)?.name||'专属代理不可用'}}</td><td><ResourceCheckSummary :check="m.check" mother/></td><td><span class="badge" :class="m.enabled?'green':'gray'">{{m.enabled?'启用':'停用'}}</span><small v-if="m.auth_blocked" class="block form-error">授权失效，暂停接单</small><small v-else-if="m.token_expires_at&amp;&amp;new Date(m.token_expires_at).getTime()&lt;=Date.now()" class="block form-error">凭证过期，暂停接单</small></td><td><button v-if="m.active_refund" class="link" @click="openOrder(m.active_refund)">{{m.active_email}} · {{statuses[m.active_status||'']||'处理中'}}</button><span v-else>空闲</span></td><td><div class="execution-actions"><button class="btn secondary" :disabled="busy" @click="checkMother(m)">校验 / 刷新席位</button><button class="btn secondary" :disabled="busy" @click="editMother(m)">编辑</button><button class="btn secondary danger" :disabled="busy" @click="removing={kind:'mothers',id:m.id,name:m.name};modal='delete';formError=''">删除</button></div></td></tr>
    <tr v-if="!mothers.length"><td colspan="7" class="empty-state">请先录入网络出口，再录入母号资源。</td></tr>
   </tbody></table></div>
   <p class="execution-footnote">删除母号后立即从配置列表移除并停止分配新申请，历史服务记录保留，已开始的申请继续按原流程收尾。</p>
  </section>
  <section v-show="section==='proxies'" id="refund-resource-proxies" role="tabpanel" class="panel">
   <div class="section-title"><div><h2>代退代理</h2><p>连接信息加密保管，列表只展示名称与可用状态。</p></div><button class="btn primary" :disabled="busy" @click="editProxy()">录入代理</button></div>
   <div class="table-scroll"><table><thead><tr><th>名称</th><th>适用对象</th><th>状态</th><th>配置</th><th>冷却释放时间</th><th>连接测试</th><th>操作</th></tr></thead><tbody>
    <tr v-for="p in proxies" :key="p.id"><td>{{p.name}}</td><td>{{targetName(p)}}</td><td><span class="badge" :class="p.enabled?'green':'gray'">{{p.enabled?'启用':'停用'}}</span></td><td>{{settings.child_proxy_id===p.id?'子号主用':settings.child_proxy_backups.includes(p.id)?'子号备用 '+(settings.child_proxy_backups.indexOf(p.id)+1):'连接信息已保存'}}</td><td><ProxyCooldown :until="p.failure_until"/></td><td><ResourceCheckSummary :check="p.check"/></td><td><div class="execution-actions"><button class="btn secondary" :disabled="busy" @click="checkProxy(p)">测试代理</button><button class="btn secondary" :disabled="busy" @click="editProxy(p)">编辑</button><button class="btn secondary danger" :disabled="busy" @click="removing={kind:'proxies',id:p.id,name:p.name};modal='delete';formError=''">删除</button></div></td></tr>
    <tr v-if="!proxies.length"><td colspan="7" class="empty-state">还没有网络出口配置。</td></tr>
   </tbody></table></div>
  </section>
 </div>
 <Modal v-if="modal" :title="modal==='mother'?'母号配置':modal==='proxy'?'代理配置':'删除配置'" :busy="busy" @close="!busy&&close()">
  <form class="execution-form" @submit.prevent="save">
   <template v-if="modal==='mother'">
    <label>母号名称<input v-model="mother.name" required maxlength="100"/></label>
    <label>Session JSON / Access Token<textarea v-model="mother.session" rows="5" autocomplete="off" spellcheck="false" :disabled="busy||parsing" :required="!mother.id" :placeholder="mother.id?'留空保留已保存凭据':'粘贴 Session JSON 或母号 AT，自动读取账号信息'"/></label>
    <input ref="fileInput" hidden type="file" accept=".json,application/json" @change="importMother"/>
    <div class="execution-actions"><button type="button" class="btn secondary" :disabled="busy||parsing" @click="fileInput?.click()">导入 JSON</button><button type="button" class="btn secondary" :disabled="busy||parsing" @click="parseMother">{{parsing?'解析中…':'解析凭据'}}</button><button type="button" class="btn secondary" :disabled="busy||parsing" @click="checkMother()">校验 / 刷新席位</button></div>
    <ResourceCheckSummary v-if="motherCheck" :check="motherCheck" mother/>
    <p v-if="motherPreview" class="muted">{{motherPreview.email||'未读取到邮箱'}} · {{motherPreview.plan_type||'未提供计划'}}<br/>{{motherPreview.expires_at?`AT 到期：${displayTime(motherPreview.expires_at)}`:'AT 未提供到期时间'}}{{motherPreview.has_refresh?' · 已读取 RT':''}}。解析仅提取资料，校验用于确认可用性。</p>
    <label>母号邮箱<input v-model="mother.email" type="email" placeholder="解析后自动填写"/></label>
    <label>团队 ID<input v-model="mother.team_id" placeholder="自动读取，也可手工填写"/></label>
    <label>Refresh Token（可选）<input v-model="mother.refresh_token" type="password" autocomplete="new-password" :placeholder="mother.has_refresh?'已保存，留空保留':'可从 Session JSON 自动提取'"/></label>
    <label>母号专属代理<select v-model="mother.proxy_id" required><option value="" disabled>请选择母号专属代理</option><option v-for="p in motherProxies" :key="p.id" :value="p.id">{{p.name}}{{p.enabled?'':'（已停用）'}}</option></select></label>
    <p class="muted">母号的校验和业务请求均使用此专属代理，子号使用上方的全局代理。凭据加密保存，编辑时留空保留。</p>
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
   <footer class="modal-actions"><button type="button" class="btn secondary" :disabled="busy||parsing" @click="close">取消</button><button class="btn primary" :disabled="busy||parsing">{{busy?'处理中…':modal==='delete'?'确认删除':'保存'}}</button></footer>
  </form>
 </Modal>
 
</template>

<style scoped>
.refund-resource-overview{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;margin-bottom:28px}.refund-resource-overview article{padding:22px}.refund-resource-overview span{display:block;font-size:11px;color:var(--muted);margin-bottom:15px}.refund-resource-overview b{display:block;font-size:28px}.refund-resource-overview small{font-size:11px;font-weight:400;color:var(--muted)}.refund-resource-overview b.queue-state{font-size:16px;margin-bottom:10px}.refund-resource-tabs{margin-bottom:22px}.refund-resource-tabs button{padding:13px 22px}@media(max-width:650px){.refund-resource-overview{grid-template-columns:1fr 1fr;gap:10px}.refund-resource-overview article{padding:17px}.refund-resource-overview article:last-child{grid-column:1/-1}.refund-resource-tabs button{padding:12px 16px}}

.execution-settings{display:grid;gap:22px}.panel{padding:24px;min-width:0;overflow:auto}.section-title,.execution-actions{display:flex;justify-content:space-between;gap:14px;align-items:center;flex-wrap:wrap}.section-title p{color:var(--muted);font-size:12px;margin:8px 0 20px}.execution-form{display:grid;gap:16px}.execution-form label{display:grid;gap:8px;font-size:12px}.execution-form .checkbox-label{display:flex;align-items:center;gap:10px}.checkbox-label input{width:auto}.form-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px}.muted{font-size:12px;line-height:1.8;margin:0}.recovery-settings{border:1px solid var(--line);border-radius:12px;padding:20px;display:grid;gap:16px}.recovery-settings legend{padding:0 8px;font-weight:600}.error-banner,.form-error{color:#b4423c;padding:10px}.execution-form .btn{justify-self:start}.execution-settings table{width:100%;min-width:700px}.execution-settings td{vertical-align:top}.execution-actions .btn{padding:7px 12px}.modal-actions{display:flex;justify-content:flex-end;gap:12px}.execution-form input,.execution-form select,.execution-form textarea{max-width:100%;min-width:0} @media(max-width:600px){.panel{padding:16px}.recovery-settings{padding:14px}}
</style>
<style scoped>
.child-proxy-policy{border:1px solid #dce7e9;border-radius:12px;padding:20px;margin:8px 0 16px}.child-proxy-heading{display:flex;justify-content:space-between;gap:15px;align-items:center;margin-bottom:16px}.child-proxy-heading h3{font-size:15px;margin:0}.child-proxy-heading>span{font-size:11px;color:#488579}.child-proxy-fields{display:grid;grid-template-columns:2fr 1fr;gap:18px}.child-proxy-fields small{font-size:11px;font-weight:normal;color:#81929a}.child-proxy-options{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0}.child-proxy-options label{display:flex;align-items:center;background:#f7f9fa;border:1px solid #dce7e9;border-radius:8px;padding:11px 15px;gap:9px}.child-proxy-options small{display:block;font-size:11px;color:#a16a68}.child-proxy-priority{list-style:none;padding:0;display:grid;gap:8px}.child-proxy-priority li{display:flex;gap:8px;align-items:center;font-size:12px}.child-proxy-priority li>span{flex:1}.child-proxy-priority button{font-size:11px;padding:5px 9px}.child-proxy-policy>p{margin-bottom:0;line-height:1.8}@media(max-width:600px){.child-proxy-fields{grid-template-columns:1fr}.child-proxy-heading{align-items:flex-start;flex-direction:column;gap:6px}.child-proxy-policy{padding:14px}.child-proxy-options label{width:100%}}
</style>
