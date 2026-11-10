<script setup lang="ts">
import type { ResourceCheck } from './resource-check';
defineProps<{check?:ResourceCheck;mother?:boolean}>();
const time=(value:string)=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
</script>
<template>
 <div class="resource-check-summary" aria-live="polite">
  <template v-if="check">
   <span class="badge" :class="check.valid?'green':'red'">{{check.valid?'检查通过':'检查未通过'}}</span>
   <small class="block">{{check.message}}</small>
   <small v-if="check.ip_address" class="block">出口 IP：{{check.ip_address}}</small>
   <template v-if="mother">
    <small v-for="(label,key) in {standard:'普通席位',premium:'高级席位'}" :key="key" class="block">
     {{label}}：{{check.seats?.[key]?`${check.seats[key]!.remaining} / ${check.seats[key]!.total}`:'未读取'}}
     <template v-if="check.seats?.[key]?.held"> · 临停 {{check.seats[key]!.held}}</template>
    </small>
    <small v-if="check.capacity_message" class="block">{{check.capacity_message}}</small>
   </template>
   <small class="block muted">{{time(check.checked_at)}} · {{check.latency_ms}} ms</small>
  </template>
  <template v-else><span class="muted">尚未检查</span><small v-if="mother" class="block muted">席位未读取</small></template>
 </div>
</template>
<style scoped>
.resource-check-summary{min-width:180px;max-width:340px;line-height:1.7;padding:4px 0;overflow-wrap:anywhere}
</style>
