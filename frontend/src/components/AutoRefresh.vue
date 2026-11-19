<script setup lang="ts">
import {onMounted,onUnmounted,ref,watch} from 'vue'
import {RefreshCw} from 'lucide-vue-next'
import {rememberList} from '../listState'
const props=defineProps<{storageKey:string;busy?:boolean;paused?:boolean}>()
const emit=defineEmits<{refresh:[quiet:boolean]}>()
const enabled=ref(true),seconds=ref(10),hidden=ref(document.hidden)
rememberList('auto-refresh-'+props.storageKey,{enabled,seconds})
if(![5,10,30,60].includes(seconds.value))seconds.value=10
let timer:ReturnType<typeof setInterval>|undefined,nextDue=Date.now()+seconds.value*1000
// Keep a deadline independent of incidental parent requests. Repeated busy
// changes must not continually postpone the next refresh.
function tick(){if(enabled.value&&!hidden.value&&!props.busy&&!props.paused&&Date.now()>=nextDue){nextDue=Date.now()+seconds.value*1000;emit('refresh',true)}}
function visible(){hidden.value=document.hidden;if(!hidden.value){nextDue=Date.now();tick()}}
function manual(){if(props.busy)return;nextDue=Date.now()+seconds.value*1000;emit('refresh',false)}
watch([enabled,seconds],()=>{nextDue=Date.now()+seconds.value*1000})
watch(()=>props.busy,busy=>{if(!busy)tick()})
watch(()=>props.paused,(paused,previous)=>{if(previous&&!paused){nextDue=Date.now();tick()}})
onMounted(()=>{hidden.value=document.hidden;document.addEventListener('visibilitychange',visible);timer=setInterval(tick,1000)})
onUnmounted(()=>{clearInterval(timer);document.removeEventListener('visibilitychange',visible)})
</script>
<template><div class="auto-refresh"><label><input v-model="enabled" type="checkbox"/>自动刷新</label><select v-model.number="seconds" aria-label="自动刷新间隔" :disabled="!enabled"><option v-for="s in [5,10,30,60]" :key="s" :value="s">{{s}} 秒</option></select><button type="button" class="btn secondary" :disabled="busy" @click="manual"><RefreshCw :size="15" :class="{spinning:busy}"/>立即刷新</button><span v-if="enabled&&(hidden||paused)" class="auto-paused">已暂停</span></div></template>
<style scoped>
.auto-refresh{display:flex;align-items:center;flex-wrap:wrap;gap:8px}.auto-refresh label{display:flex;flex-direction:row;align-items:center;gap:6px;font-size:11px;white-space:nowrap;margin:0;color:#5c756a}.auto-refresh input{width:14px;height:14px;margin:0;accent-color:#34745a}.auto-refresh select{width:75px;margin:0;padding:9px 10px;font-size:11px;border-radius:8px;background:white;height:36px}.auto-refresh .btn{height:36px;padding:8px 12px;font-size:11px;white-space:nowrap}.auto-paused{font-size:10px;color:#8b9e94}@media(max-width:600px){.auto-refresh{gap:6px}.auto-refresh select{width:65px;padding:8px 5px}.auto-refresh .btn{padding:8px}.auto-refresh label{font-size:10px}}
</style>
