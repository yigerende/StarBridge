<script setup lang="ts">
import {onBeforeUnmount,ref,watch} from 'vue'
import {Copy,Eye,EyeOff,KeyRound} from 'lucide-vue-next'
import {api} from '../lib'
const props=defineProps<{id:string}>()
type Credentials={email:string;password:string;totp_secret:string}
const value=ref<Credentials|null>(null),busy=ref(false),error=ref(''),copied=ref('')
let controller:AbortController|undefined,generation=0
function clear(){generation++;controller?.abort();controller=undefined;value.value=null;busy.value=false;error.value='';copied.value=''}
async function reveal(){
 clear();const run=++generation;controller=new AbortController();busy.value=true
 try{const data=await api(`/admin/refund/orders/${encodeURIComponent(props.id)}/credentials`,'GET',undefined,{},controller.signal);if(run===generation)value.value=data}
 catch(e){if(run===generation)error.value=(e as Error).message}
 finally{if(run===generation)busy.value=false}
}
async function copy(field:'password'|'totp_secret'){
 if(!value.value)return
 const run=generation
 try{await navigator.clipboard.writeText(value.value[field]);if(run===generation){copied.value=field;error.value=''}}
 catch{if(run===generation)error.value='复制失败，请手动选择内容复制。'}
}
watch(()=>props.id,clear)
onBeforeUnmount(clear)
</script>
<template>
 <section class="refund-credentials" aria-label="账号资料">
  <header><h4><KeyRound :size="16"/>账号资料</h4><button class="text-btn" :disabled="busy" @click="value?clear():reveal()"><EyeOff v-if="value" :size="15"/><Eye v-else :size="15"/>{{value?'隐藏资料':busy?'正在读取…':'查看密码和 2FA'}}</button></header>
  <p v-if="error" class="credentials-error" role="alert">{{error}}</p>
  <div v-if="value" class="credential-fields">
   <div v-for="[field,label] in ([['password','密码'],['totp_secret','2FA 密钥']] as const)" :key="field" class="credential-field"><span>{{label}}</span><div class="credential-value"><code>{{value[field]}}</code><button class="icon-btn" :aria-label="`复制${label}`" @click="copy(field)"><Copy :size="15"/></button></div><small v-if="copied===field" role="status">已复制</small></div>
  </div>
  <p v-else-if="!error" class="credentials-note">查看本单提交的密码和 2FA 密钥。</p>
 </section>
</template>
<style scoped>
.refund-credentials{border:1px solid var(--line);border-radius:12px;padding:18px 20px;min-width:0}.refund-credentials header{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.refund-credentials h4{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:500;color:var(--muted);margin:0}.refund-credentials .text-btn{display:flex;align-items:center;gap:6px;font-size:12px}.credentials-note{font-size:11px;color:var(--muted);margin:14px 0 0}.credential-fields{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:16px}.credential-field{min-width:0}.credential-field>span{font-size:11px;color:var(--muted)}.credential-value{display:flex;align-items:flex-start;gap:10px;margin-top:8px;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--surface-soft,#f8fafc)}.credential-value code{white-space:pre-wrap;overflow-wrap:anywhere;min-width:0;flex:1;font-size:12px;line-height:1.8;user-select:all}.credential-value button{flex:none}.credential-field small{font-size:11px;color:#397856;display:block;margin-top:6px}.credentials-error{font-size:12px;color:#ad4138;margin:14px 0 0}@media(max-width:600px){.refund-credentials{padding:15px 17px}.credential-fields{grid-template-columns:1fr}}
</style>
