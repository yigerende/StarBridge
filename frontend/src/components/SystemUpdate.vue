<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import {RefreshCw,Download,Power,ExternalLink} from 'lucide-vue-next'
import {systemUpdate as state,updateError} from '../systemUpdate'
import {updateBusy,updateSubmitting,watchUpdates,updateAction} from '../systemUpdate'
const props=defineProps<{compact?:boolean}>()
const opened=ref(false),address=ref(''),dirty=ref(false),now=ref(Date.now())
watch(()=>state.value.public_origin,value=>{if(!dirty.value)address.value=value||location.origin},{immediate:true})
const busy=computed(()=>updateSubmitting.value||state.value.pending||updateBusy.has(state.value.phase))
const short=(v:string)=>v&&v!=='local'?v.slice(0,8):'本地版本'
const restarting=computed(()=>['switching','verifying','rolling_back'].includes(state.value.phase))
const seconds=computed(()=>Math.max(0,10-Math.floor((now.value/1000-(state.value.restart_started_at||now.value/1000)))))
let stop:()=>void,timer:ReturnType<typeof setInterval>
onMounted(()=>{stop=watchUpdates();timer=setInterval(()=>now.value=Date.now(),1000)})
onUnmounted(()=>{stop?.();clearInterval(timer)})
async function prepare(){await updateAction('prepare',{public_origin:address.value});dirty.value=false}
</script>
<template><div :class="['system-update',compact?'compact':'panel']">
 <button v-if="compact" class="version-badge" @click="opened=!opened" :aria-expanded="opened" title="版本与更新">{{short(state.current_version)}}<span v-if="state.latest_version&&state.latest_version!==state.current_version" class="version-dot"/></button>
 <section v-if="!compact||opened" :class="{'version-popover':compact}">
  <div class="version-heading"><div><h2>版本与更新</h2><p>当前版本 <b class="mono">{{short(state.current_version)}}</b></p></div><a :href="state.repository_url||'https://github.com/yigerende/StarBridge'" target="_blank" rel="noopener noreferrer" aria-label="查看 GitHub 仓库"><ExternalLink :size="17"/></a></div>
  <label v-if="!compact">站点访问地址<input v-model.trim="address" type="url" placeholder="https://bridge.example.com" :disabled="busy" @input="dirty=true"/><small>更换域名后填入完整地址，更新准备完成后点击立即重启生效。</small></label>
  <p v-if="state.latest_version" class="muted">GitHub 最新版本：<a :href="(state.repository_url||'https://github.com/yigerende/StarBridge')+'/commit/'+state.latest_version" target="_blank" rel="noopener noreferrer">{{short(state.latest_version)}}</a></p>
  <p v-if="state.message" role="status">{{state.message}}</p>
  <p v-if="restarting" class="update-status">{{seconds?`正在重启，${seconds} 秒后继续确认…`:'正在等待服务恢复…'}}</p>
  <p v-if="state.phase==='ready'" class="callout">版本已准备好，立即重启后生效。{{state.prepared_origin!==state.public_origin?'访问地址将改为 '+state.prepared_origin:''}}</p>
  <p v-if="updateError||state.error" class="alert danger" role="alert">{{updateError||state.error}}</p>
  <p v-if="state.available===false" class="muted">更新服务尚未连接。</p>
  <div class="update-actions"><button class="btn secondary" :disabled="busy||!state.available" @click="updateAction('check')"><RefreshCw :size="15"/>检查更新</button><button class="btn primary" :disabled="busy||!state.available||!address" @click="prepare"><Download :size="15"/>更新</button><button v-if="state.phase==='ready'" class="btn primary" :disabled="busy" @click="updateAction('restart',{prepared_id:state.prepared_id})"><Power :size="15"/>立即重启</button></div>
  <small class="muted">更新时准备新版本，网站继续使用。重启前等待在途任务结束，完成后自动确认服务状态。</small>
  <details v-if="state.rollback_versions?.length" class="rollback"><summary>回退版本</summary><div v-for="v in state.rollback_versions" :key="v.id"><span class="mono">{{short(v.version)}}</span><button class="text-btn" :disabled="busy||!state.available||!v.compatible" @click="updateAction('rollback',{version_id:v.id})">回退并重启</button></div></details>
 </section>
</div></template>
<style scoped>
.system-update{position:relative}.system-update.panel{padding:24px;margin-top:24px}.version-badge{display:flex;align-items:center;gap:6px;padding:4px 8px;border:1px solid var(--line);border-radius:6px;color:var(--muted);font-size:11px}.version-dot{width:6px;height:6px;background:var(--green);border-radius:50%}.version-popover{position:absolute;left:0;top:36px;z-index:160;width:360px;max-width:calc(100vw - 32px);padding:22px;background:var(--card,#fff);border:1px solid var(--line);border-radius:14px;box-shadow:0 15px 50px #14291f22}.version-heading{display:flex;justify-content:space-between;align-items:start}.version-heading h2{font-size:18px;margin-bottom:8px}.system-update p,.system-update label{font-size:13px}.system-update small{display:block;line-height:1.7}.update-actions{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}.update-status{color:var(--green)}.rollback{margin-top:18px}.rollback summary{cursor:pointer}.rollback>div{display:flex;justify-content:space-between;padding:10px 0}@media(max-width:700px){.compact{display:none}}
</style>
