<script setup lang="ts">
import {ref} from 'vue'
import {Save} from 'lucide-vue-next'
import {api,notify,type Row} from '../lib'
const props=defineProps<{value:Row;products:Row[]}>(),emit=defineEmits<{saved:[]}>()
const form=ref<Row>({registration_standard:true,registration_oauth:false,refund:true,...props.value,subscription:{...props.value.subscription}}),busy=ref(false),error=ref('')
for(const p of props.products)form.value.subscription[p.id]??=true
async function save(){if(busy.value)return;busy.value=true;error.value='';try{await api('/admin/settings/membership_pricing','PUT',form.value);notify('会员减免范围已保存');emit('saved')}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
</script>
<template><form class="membership-benefits panel" @submit.prevent="save"><header><h3>会员减免范围</h3><p>开启后按会员等级减免；关闭后按标准价计费。账户专属价格始终优先，已受理订单保留原价。</p></header><div class="benefit-groups">
 <section><h4>注册</h4><label><span>不需要 OAuth<small>邮箱、密码与 2FA</small></span><input v-model="form.registration_standard" type="checkbox" aria-label="不需要 OAuth 参与会员减免"/></label><label><span>需要 OAuth<small>包含 OAuth 授权资料</small></span><input v-model="form.registration_oauth" type="checkbox" aria-label="需要 OAuth 参与会员减免"/></label></section>
 <section><h4>订阅</h4><label v-for="p in products" :key="p.id"><span>{{p.name}}</span><input v-model="form.subscription[p.id]" type="checkbox" :aria-label="p.name+' 参与会员减免'"/></label><p v-if="!products.length">暂无启用的套餐</p></section>
 <section><h4>代退</h4><label><span>代退服务费</span><input v-model="form.refund" type="checkbox" aria-label="代退参与会员减免"/></label></section>
 </div><p v-if="error" class="alert danger" role="alert">{{error}}</p><footer><button class="btn primary" :disabled="busy"><Save :size="16"/>保存减免范围</button></footer></form></template>
<style scoped>.membership-benefits{padding:24px;margin-bottom:22px}.membership-benefits header p{font-size:12px;color:var(--muted);line-height:1.8;margin:10px 0 22px}.benefit-groups{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px}.benefit-groups section{border:1px solid var(--line);border-radius:12px;padding:18px}.benefit-groups h4{margin:0 0 14px;font-size:14px}.benefit-groups label{display:flex;flex-direction:row;align-items:center;justify-content:space-between;gap:16px;padding:14px 0;margin:0;border-top:1px solid var(--line);font-size:12px}.benefit-groups small{display:block;margin-top:7px;color:var(--muted);font-size:11px}.benefit-groups input{width:18px;height:18px;accent-color:var(--green);flex-shrink:0}.membership-benefits footer{display:flex;justify-content:flex-end;margin-top:22px}@media(max-width:800px){.benefit-groups{grid-template-columns:1fr}.membership-benefits{padding:16px}}</style>
