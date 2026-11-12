<script setup lang="ts">
import {onUnmounted,ref,watch} from 'vue';
import {Check,Clock3,RefreshCw,ListChecks} from 'lucide-vue-next';
import {api as bridgeAPI} from '../lib';
async function api<T>(path:string,options?:{signal?:AbortSignal}):Promise<T>{return bridgeAPI(path,'GET',undefined,{},options?.signal) as Promise<T>}
const emit=defineEmits<{loaded:[value:any]}>();
type PersonalPlan={status:string;label:string;fields?:{name:string;value:string}[]};
type Item={stage:string;attempt:number;status:string;started_at:string|null;finished_at:string|null;duration_seconds:number|null;reason:string;personal_plan?:PersonalPlan};
type Recovery={pending:boolean;running:boolean;alert:boolean;label:string;reason:string;started_at:string|null;next_at:string|null;retries:number;checks:number;cleanups:number;switches:number;fallbacks:number};
type Timeline={recovery?:Recovery;items:Item[];active:boolean;generated_at:string};
const props=defineProps<{id:string;status:string}>();
const data=ref<Timeline>(),error=ref(''),loading=ref(false);
let controller:AbortController|undefined,timer:ReturnType<typeof setTimeout>|undefined,sequence=0,disposed=false;
function timestamp(value:string|null){return value?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value)):'未记录'}
function duration(n:number|null){if(n===null)return '未记录';if(n<1)return '不足 1 秒';if(n<60)return `${n} 秒`;return `${Math.floor(n/60)} 分 ${n%60} 秒`}
function tone(state:string){if(['已完成','已确认','已受理'].includes(state))return 'done';if(['进行中','排队中'].includes(state))return 'running';if(['待跟进','待核实','未受理'].includes(state))return 'attention';return 'muted'}
const stageNames:Record<string,string>={'提':'提交申请','候':'排队调度','查':'检查母号','邀':'发送邀请','登':'账号登录','团':'确认团队','停':'提交停用','移':'移出与清理','结':'办理结束','验':'套餐核验','核':'结果核实','复':'重新办理'};
function planDetails(plan:PersonalPlan){return plan.fields?.map(field=>`${field.name}：${field.value}`).join('\n')||plan.label}
async function load(background=false){
 clearTimeout(timer);if(disposed)return;
 if(background&&document.hidden){timer=setTimeout(()=>load(true),10000);return}
 controller?.abort();controller=new AbortController();const current=++sequence;
 loading.value=true;error.value='';
 try{const result=await api<Timeline>(`/admin/refund/orders/${encodeURIComponent(props.id)}/timeline`,{signal:controller.signal});if(current===sequence){data.value=result;emit('loaded',result)}}
 catch(e){if(current===sequence&&(e as Error).name!=='AbortError')error.value='读取失败，请重新读取记录'}
 finally{if(current===sequence&&!disposed){loading.value=false;if(data.value?.active)timer=setTimeout(()=>load(true),10000)}}
}
watch(()=>[props.id,props.status],()=>{data.value=undefined;load()},{immediate:true});
onUnmounted(()=>{disposed=true;sequence++;controller?.abort();clearTimeout(timer)});
</script>
<template>
 <section class="stage-monitor" aria-label="办理流程记录" :aria-busy="loading">
  <header><div><h4><ListChecks :size="17"/>办理流程</h4><p>北京时间 · {{data?.active?'每 10 秒自动更新':'完整流程记录'}}</p></div><button class="text-btn" :disabled="loading" @click="load()"><RefreshCw :size="13"/>{{loading?'读取中…':'刷新流程记录'}}</button></header>
  <p v-if="error" class="form-error" role="alert">{{error}}</p>
  <div v-if="data?.recovery?.started_at" class="recovery-status" :class="{alert:data.recovery.alert}">
   <strong>{{data.recovery.label}}{{data.recovery.running?' · 正在执行':''}}</strong>
   <span v-if="data.recovery.alert">异常占用已超过告警阈值，请跟进</span>
   <p>{{data.recovery.reason}}</p>
   <p>首次异常 {{timestamp(data.recovery.started_at)}}<br/>{{data.recovery.next_at?'下次执行 '+timestamp(data.recovery.next_at):data.recovery.pending?'当前正在核对':'没有待执行的自动任务'}}</p>
   <small>已重试 {{data.recovery.retries}} 次 · 已核实 {{data.recovery.checks}} 轮 · 已清理核对 {{data.recovery.cleanups}} 轮 · 已兜底 {{data.recovery.fallbacks||0}} 轮<span v-if="data.recovery.switches"> · 已安排换号 {{data.recovery.switches}} 次</span></small>
  </div>
  <ol v-if="data" class="stage-records">
   <li v-for="(row,index) in data.items" :key="index" :class="tone(row.status)">
    <span class="stage-mark" aria-hidden="true"><Check v-if="tone(row.status)==='done'" :size="14"/><Clock3 v-else-if="tone(row.status)==='running'" :size="14"/><span v-else>{{index+1}}</span></span>
    <div class="stage-copy"><div class="stage-top"><strong>{{stageNames[row.stage]||row.stage}}</strong><span class="stage-state" :class="tone(row.status)">{{row.status}}</span><small v-if="row.attempt>1">第 {{row.attempt}} 次</small><span v-if="row.stage==='登'&&row.personal_plan" class="personal-plan" :class="{observed:row.personal_plan.status==='observed'}" :title="planDetails(row.personal_plan)" tabindex="0">{{row.personal_plan.label}}</span><time v-if="row.finished_at||row.started_at">{{timestamp(row.finished_at||row.started_at)}}</time></div>
     <div class="stage-meta"><span v-if="row.duration_seconds!==null">耗时 {{duration(row.duration_seconds)}}</span><span v-if="row.started_at&&row.finished_at">开始 {{timestamp(row.started_at)}}</span><span v-if="row.reason" class="stage-reason">{{row.reason}}</span></div>
    </div>
   </li>
  </ol>
  <p v-if="!data&&!error" class="stage-footnote" role="status">正在读取办理流程…</p><p v-else-if="data&&!data.items.length" class="stage-footnote">暂无流程记录</p>
 </section>
</template>
<style scoped>
.stage-monitor{border:1px solid var(--line);border-radius:12px;overflow:hidden}.stage-monitor header{display:flex;justify-content:space-between;align-items:center;gap:14px;padding:16px 18px;background:#f8fafc;border-bottom:1px solid var(--line)}.stage-monitor h4{display:flex;align-items:center;gap:8px;font-size:13px;margin:0}.stage-monitor header p{color:var(--muted);font-size:10px;margin:6px 0 0}.stage-monitor header button{font-size:11px;white-space:nowrap;display:flex;align-items:center;gap:5px}.stage-records{list-style:none;padding:6px 18px;margin:0;max-height:380px;overflow:auto}.stage-records li{display:flex;gap:12px;position:relative;padding:12px 0}.stage-records li+li{border-top:1px solid #eef2f6}.stage-mark{display:grid;place-items:center;flex:none;width:26px;height:26px;border:1px solid #e3e9ef;border-radius:8px;background:#f5f7f9;color:#96a3b0;font-size:10px;font-variant-numeric:tabular-nums}.done .stage-mark{background:#edf7f1;border-color:#d9ebdf;color:#4a8463}.running .stage-mark{background:#edf4fd;border-color:#dce6f6;color:#4e7cb0}.attention .stage-mark{background:#fff6e7;border-color:#efe0c0;color:#a27531}.stage-copy{min-width:0;flex:1}.stage-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-height:26px}.stage-top strong{font-size:12px;font-weight:550}.stage-state{font-size:10px;padding:2px 6px;border-radius:4px;color:#8291a1;background:#f4f6f8}.stage-state.done{color:#51806a;background:#f0f7f3}.stage-state.running{color:#527fae;background:#edf4fd}.stage-state.attention{color:#9b762d;background:#fff6e6}.stage-top time{margin-left:auto;font-size:10px;color:var(--muted);font-variant-numeric:tabular-nums}.stage-top small{color:var(--muted);font-size:10px}.stage-meta{display:flex;gap:6px 14px;flex-wrap:wrap;font-size:10px;color:var(--muted);line-height:1.7}.stage-meta:not(:empty){margin-top:4px}.stage-reason{width:100%;overflow-wrap:anywhere}.stage-footnote{color:var(--muted);font-size:11px;padding:10px 18px}.personal-plan{font-size:10px;padding:2px 6px;border-radius:4px;background:#f4f6f8;color:#78889a;max-width:100%;overflow-wrap:anywhere}.personal-plan.observed{background:#edf7f1;color:#51806a}.personal-plan:focus-visible{outline:2px solid #527fae;outline-offset:2px}.recovery-status{margin:14px 18px;padding:13px 15px;border:1px solid #dce6f6;background:#f4f8fe;border-radius:8px;font-size:11px;line-height:1.8}.recovery-status.alert{border-color:#ebd7b4;background:#fff9ee}.recovery-status strong{font-size:12px}.recovery-status>span{display:block;color:#a27531}.recovery-status p{margin:5px 0}.recovery-status small{color:var(--muted);font-size:10px}.form-error{padding:0 18px;color:#ad4138;font-size:12px}@media(max-width:600px){.stage-monitor header{padding:14px;align-items:flex-start}.stage-records{padding:4px 14px}.stage-records li{gap:9px}.stage-top time{width:100%;margin-left:0}.recovery-status{margin:12px}.stage-top{gap:5px 7px}}
</style>
