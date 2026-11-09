<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {notify,type Row} from '../lib'
const data=ref<Row>(),error=ref(''),busy=ref(false)
async function request(method='GET',body?:Row){const r=await fetch('/registration/api/admin/v1/settings',{method,headers:{'X-Requested-With':'gptreg','Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});const value=await r.json();if(!r.ok)throw new Error(value.error||'操作未完成');return value}
onMounted(async()=>{try{data.value=await request()}catch(e){error.value=(e as Error).message}})
async function save(){if(!data.value)return;busy.value=true;error.value='';try{await request('PATCH',{orders_open:data.value.orders_open,worker_enabled:data.value.worker_enabled,api_enabled:data.value.api_enabled,features:data.value.features});notify('注册功能开关已保存')}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
</script>
<template><section class="panel form-panel registration-controls"><h2>注册服务开关</h2><p class="muted">注册与订阅独立接单和调度，共用账户、积分和站点管理。</p><p v-if="error" class="alert danger">{{error}}</p><form v-if="data" @submit.prevent="save"><div class="feature-grid"><label v-for="[key,label] in [['orders_open','受理注册订单'],['worker_enabled','运行注册队列'],['api_enabled','开放注册 API']]" :key="key" class="checkbox-label"><input type="checkbox" v-model="data[key]"/>{{label}}</label><label v-for="[key,label] in [['register','显示注册办理'],['orders','查看注册订单'],['accounts','查看账号交付']]" :key="key" class="checkbox-label"><input type="checkbox" v-model="data.features[key]"/>{{label}}</label></div><button class="btn primary" :disabled="busy">保存注册开关</button></form></section></template>
<style scoped>.registration-controls{margin-bottom:20px}.feature-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin:20px 0}</style>
