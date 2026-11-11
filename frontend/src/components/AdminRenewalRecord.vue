<script setup lang="ts">
import {onMounted,onUnmounted,ref} from 'vue'
import {api,date,labels,type Row} from '../lib'
import Modal from './Modal.vue'
const props=defineProps<{id:string}>(),emit=defineEmits<{close:[]}>()
const detail=ref<Row>(),error=ref(''),loading=ref(false)
let controller:AbortController|undefined
async function load(){controller?.abort();controller=new AbortController();loading.value=true;error.value='';try{detail.value=await api('/admin/renewals/'+encodeURIComponent(props.id),'GET',undefined,{},controller.signal)}catch(e){if((e as Error).name!=='AbortError')error.value=(e as Error).message}finally{loading.value=false}}
onMounted(load);onUnmounted(()=>controller?.abort())
</script>
<template><Modal title="续费任务完整记录" wide @close="emit('close')"><p v-if="error" class="alert danger" role="alert">{{error}}</p><button class="btn secondary" :disabled="loading" @click="load">刷新记录</button><template v-if="detail"><div class="detail-title"><div><h3>{{detail.task.email}}</h3><p>{{detail.task.owner}}</p><small class="mono">{{detail.task.id}}</small></div><span class="badge">{{labels[detail.task.status]||detail.task.status}}</span></div><p>{{detail.task.message}}</p><div class="timeline"><div v-for="(event,i) in detail.events" :key="i"><span class="timeline-dot"/><small>{{date(event.created_at)}}</small><p>{{event.message}}</p></div></div></template><p v-else-if="!error" role="status">正在读取完整记录…</p></Modal></template>
