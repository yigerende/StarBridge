<script setup lang="ts">
import {onMounted,onUnmounted,ref,watch} from 'vue'
import type {Row} from '../lib'
const props=defineProps<{route:string;search?:string;enabled:boolean;user:Row;pub:Row;financeFilter?:Row}>()
const emit=defineEmits<{refresh:[];expired:[];navigate:[path:string]}>()
const host=ref<HTMLElement>(),error=ref('')
let disposed=false,view:ReturnType<typeof import('../registration/native')['mountRegistration']>|undefined
const values=()=>({...props,navigate:(path:string)=>emit('navigate',path),expired:()=>emit('expired')})
async function load(){
 if(!props.enabled||!host.value)return
 try{const module=await import('../registration/native');if(!disposed&&host.value){view?.unmount();view=module.mountRegistration(host.value,values())}}
 catch(e){error.value=(e as Error).message}
}
function navigate(e:Event){const path=(e as CustomEvent).detail;if(typeof path==='string'&&path.startsWith('/')&&!path.startsWith('//'))emit('navigate',path)}
const refresh=()=>emit('refresh'),expired=()=>emit('expired')
onMounted(()=>{void load();window.addEventListener('registration:navigate',navigate);window.addEventListener('registration:wallet-changed',refresh);window.addEventListener('gptreg:session-expired',expired)})
watch(()=>[props.route,props.search,props.user,props.pub,props.financeFilter],()=>view?.update(values()))
onUnmounted(()=>{disposed=true;view?.unmount();window.removeEventListener('registration:navigate',navigate);window.removeEventListener('registration:wallet-changed',refresh);window.removeEventListener('gptreg:session-expired',expired)})
</script>
<template><section class="registration-panel"><div v-if="!enabled" class="panel empty"><h2>注册服务尚未连接</h2><p>连接完成后，可在这里使用注册、账号交付及原管理台。</p></div><div v-if="error" class="alert danger">{{error}}<button @click="error='';load()">重试</button></div><div v-if="enabled" ref="host" class="portal registration-native"/></section></template>
<style scoped>.registration-panel{min-width:0;width:100%}</style>
