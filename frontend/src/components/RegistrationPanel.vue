<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref} from 'vue'
import {registrationPaths} from '../registrationNavigation'
const props=defineProps<{route:string;enabled:boolean}>()
const emit=defineEmits<{refresh:[];expired:[];navigate:[path:string]}>()
const loading=ref(true),frame=ref<HTMLIFrameElement>(),frameHeight=ref(780)
const src=computed(()=>'/registration'+(registrationPaths[props.route]||'/')+'?embedded=1')
function message(event:MessageEvent){
 if(event.origin!==location.origin||event.source!==frame.value?.contentWindow)return
 if(event.data?.type==='registration:session-expired')emit('expired')
 if(event.data?.type==='registration:wallet-changed')emit('refresh')
 if(event.data?.type==='registration:resize'&&Number.isFinite(event.data.height))frameHeight.value=Math.max(480,Math.min(20000,event.data.height))
 if(event.data?.type==='registration:navigate'&&['/home','/keys','/wallet','/invites','/profile','/docs','/admin','/admin/users','/admin/invites','/register','/accounts','/orders'].includes(event.data.path))emit('navigate',event.data.path)
}
onMounted(()=>window.addEventListener('message',message));onUnmounted(()=>window.removeEventListener('message',message))
</script>
<template><section class="registration-panel">
 <div v-if="!enabled" class="panel empty"><h2>注册服务尚未连接</h2><p>连接完成后，可在这里使用注册、账号交付及原管理台。</p></div>
 <template v-else><div v-if="loading" class="registration-loading" role="status">正在打开注册工作台…</div>
 <iframe ref="frame" :src="src" :style="{height:frameHeight+'px'}" title="GPT Register 注册工作台" @load="loading=false" allow="clipboard-write"/></template>
</section></template>
<style scoped>
.registration-panel{min-width:0;width:100%}.registration-panel iframe{display:block;width:100%;height:max(780px,calc(100vh - 230px));border:0;background:transparent}.registration-loading{padding:20px;text-align:center;color:#5f7167}
@media(max-width:700px){.registration-panel iframe{height:calc(100dvh - 210px);min-height:650px}}
</style>
