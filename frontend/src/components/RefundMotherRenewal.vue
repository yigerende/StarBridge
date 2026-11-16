<script setup lang="ts">
import {computed,onMounted,ref} from 'vue'
import {api,date,type Row} from '../lib'
const props=defineProps<{mother:Row;saved?:Row}>(),emit=defineEmits<{busy:[boolean];changed:[]}>()
const value=ref<Row>(props.saved&&props.saved.team_id===props.mother.team_id?props.saved:{}),busy=ref(false),error=ref(''),confirmed=ref(false),fresh=ref(false)
const labels:Record<string,string>={enabled:'已开启',disabled:'已取消',no_subscription:'无有效订阅',checking:'待核实',failed:'未取消'}
const canCancel=computed(()=>fresh.value&&value.value.will_renew===true&&!value.value.pending&&!busy.value)
async function run(cancel=false){
 busy.value=true;emit('busy',true);error.value='';confirmed.value=false;fresh.value=false
 try{
  value.value=await api(`/admin/refund/mother-management/${props.mother.id}/renewal/${cancel?'cancel':'check'}`,'POST',cancel?{team_id:props.mother.team_id}:{})
  fresh.value=true;emit('changed')
 }catch(e){error.value=(e as Error).message}
 finally{busy.value=false;emit('busy',false)}
}
onMounted(()=>run())
</script>
<template>
 <section class="mother-renewal">
  <div class="renewal-heading"><div><span class="eyebrow">自动续费</span><h3>{{labels[value.status]||'尚未查询'}}</h3></div><span class="renewal-badge" :class="value.status">{{busy?'正在处理…':value.status==='checking'?'需要查询结果':'母号订阅'}}</span></div>
  <p v-if="error" class="alert danger" role="alert">{{error}}</p>
  <p v-else class="renewal-message" role="status">{{value.message||'正在查询当前订阅。'}}</p>
  <dl><div><dt>当前套餐到期</dt><dd>{{date(value.active_until)}}</dd></div><div><dt>最近查询</dt><dd>{{date(value.checked_at)}}</dd></div></dl>
  <div v-if="confirmed" class="renewal-confirm">
   <strong>取消“{{mother.name}}”的自动续费？</strong>
   <p>取消后不再续订，当前套餐保留到期。</p>
   <div class="renewal-actions"><button class="btn danger" :disabled="!canCancel" @click="run(true)">确认取消自动续费</button><button class="btn secondary" :disabled="busy" @click="confirmed=false">返回</button></div>
  </div>
  <div v-else class="renewal-actions"><button class="btn secondary" :disabled="busy" @click="run()">查询续费状态</button><button v-if="!value.pending&&value.status!=='disabled'&&value.status!=='no_subscription'" class="btn danger" :disabled="!canCancel" @click="confirmed=true">取消自动续费</button></div>
 </section>
</template>
<style scoped>
.mother-renewal{border:1px solid var(--line);border-radius:14px;padding:24px;display:grid;gap:20px}.renewal-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.eyebrow,dt{font-size:12px;color:var(--muted)}h3{margin:8px 0 0;font-size:24px}.renewal-badge{border-radius:20px;padding:7px 12px;background:var(--surface-soft,#f2f5f5);font-size:12px;white-space:nowrap}.renewal-badge.disabled{color:#13806a;background:#eaf7f1}.renewal-badge.checking,.renewal-badge.failed{color:#946019;background:#fff4df}.renewal-message{font-size:13px;line-height:1.7;margin:0;color:var(--muted)}dl{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;padding:20px 0;margin:0;border-block:1px solid var(--line)}dd{margin:9px 0 0;font-size:14px;overflow-wrap:anywhere}.renewal-actions{display:flex;gap:10px;flex-wrap:wrap}.renewal-confirm p{font-size:13px;color:var(--muted);margin:10px 0 18px}.renewal-confirm strong{overflow-wrap:anywhere}@media(max-width:600px){.mother-renewal{padding:18px;gap:16px}dl{grid-template-columns:1fr}.renewal-actions .btn{flex:1;white-space:nowrap}}
</style>
