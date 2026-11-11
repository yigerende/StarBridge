<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref} from 'vue'
const props=defineProps<{until?:string|null;failures?:number;probeUntil?:string|null}>()
const now=ref(Date.now()),end=computed(()=>props.until?Date.parse(props.until):NaN)
const seconds=computed(()=>Math.max(0,Math.ceil((end.value-now.value)/1000)))
const remaining=computed(()=>seconds.value>=3600?`${Math.floor(seconds.value/3600)}小时 ${Math.floor(seconds.value%3600/60)}分`:`${Math.floor(seconds.value/60)}分 ${seconds.value%60}秒`)
let timer:ReturnType<typeof setInterval>|undefined
onMounted(()=>timer=setInterval(()=>now.value=Date.now(),1000));onUnmounted(()=>clearInterval(timer))
</script>
<template><div class="proxy-cooldown"><template v-if="Number.isFinite(end)"><span class="badge" :class="seconds>0?'review':'active'">{{seconds>0?'冷却中':'冷却已结束'}}</span><small>释放：{{new Date(end).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}}</small><small v-if="seconds>0">剩余 {{remaining}}</small></template><span v-else class="muted">未在冷却</span><small v-if="failures&&!(seconds>0)">{{probeUntil&&Date.parse(probeUntil)>now?'恢复试用中':'等待恢复试用'}}</small></div></template>
<style scoped>.proxy-cooldown{min-width:155px;font-size:12px;white-space:nowrap}.proxy-cooldown small{display:block;margin-top:7px;color:var(--muted);font-size:11px}</style>
