<script setup lang="ts">
import {ref,watch,onMounted} from 'vue'
import {Settings2,Save,ArrowUpRight} from 'lucide-vue-next'
import {api,notify,type Row} from '../lib'
import SupportContact from './SupportContact.vue'
import Modal from './Modal.vue'
const props=defineProps<{site:Row}>(),emit=defineEmits<{saved:[]}>()
const current=ref<Row>({...props.site}),editing=ref(false),busy=ref(false),error=ref(''),form=ref({support_url:'',telegram_group_url:''})
watch(()=>props.site,value=>{current.value={...value,support_url:current.value.support_url,telegram_group_url:current.value.telegram_group_url}})
onMounted(async()=>{try{current.value=(await api('/admin/settings')).site}catch(e){error.value=(e as Error).message}})
function edit(){form.value={support_url:current.value.support_url||'',telegram_group_url:current.value.telegram_group_url||''};error.value='';editing.value=true}
async function save(){
 if(busy.value)return
 busy.value=true;error.value=''
 try{const result=await api('/admin/support','PATCH',form.value);current.value={...current.value,...result};editing.value=false;emit('saved');notify('联系方式已保存')}
 catch(e){error.value=(e as Error).message}
 finally{busy.value=false}
}
</script>
<template>
 <p v-if="error&&!editing" class="alert danger" role="alert">{{error}}</p>
 <SupportContact :site="current"><template #actions><a href="#/support" target="_blank" rel="noopener noreferrer" class="btn secondary">查看用户页面<ArrowUpRight :size="15"/></a><button class="btn primary" @click="edit"><Settings2 :size="16"/>配置联系方式</button></template></SupportContact>
 <Modal v-if="editing" title="配置联系方式" @close="!busy&&(editing=false)"><form @submit.prevent="save"><p class="muted support-config-note">保存后同步显示到用户端“联系客服”。留空可关闭对应联系按钮。</p><p v-if="error" class="alert danger" role="alert">{{error}}</p><label>客服账号或链接<input v-model="form.support_url" aria-label="客服账号或链接" maxlength="500" :disabled="busy" placeholder="@客服账号 或 https://t.me/账号" autocomplete="off"/><small>支持 Telegram 账号，也可填写其他客服网站的 http(s) 链接。</small></label><label>TG 群组账号或链接<input v-model="form.telegram_group_url" aria-label="TG 群组账号或链接" maxlength="500" :disabled="busy" placeholder="@群组账号 或 https://t.me/+邀请链接" autocomplete="off"/><small>支持公开群组账号和私密群组邀请链接。</small></label><div class="form-footer"><button type="button" class="btn secondary" :disabled="busy" @click="editing=false">取消</button><button class="btn primary" :disabled="busy"><Save :size="16"/>{{busy?'保存中…':'保存联系方式'}}</button></div></form></Modal>
</template>
<style scoped>.support-config-note{margin-bottom:22px;font-size:13px}</style>
