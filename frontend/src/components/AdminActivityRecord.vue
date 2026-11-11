<script setup lang="ts">
import {defineAsyncComponent} from 'vue'
import type {Row} from '../lib'
const props=defineProps<{record:Row;user:Row;pub:Row}>()
const emit=defineEmits<{close:[];refresh:[]}>()
const Admin=defineAsyncComponent(()=>import('../Admin.vue'))
const RefundOrders=defineAsyncComponent(()=>import('./RefundOrders.vue'))
const RegistrationPanel=defineAsyncComponent(()=>import('./RegistrationPanel.vue'))
const AdminRenewalRecord=defineAsyncComponent(()=>import('./AdminRenewalRecord.vue'))
const search='?order_id='+encodeURIComponent(props.record.id)
</script>
<template>
 <AdminRenewalRecord v-if="record.workflow==='renewal'" :id="record.id" @close="emit('close')"/>
 <RegistrationPanel v-else-if="record.business==='registration'" route="/admin/orders" :search="search" :detail-id="record.id" :enabled="true" :user="user" :pub="pub" @close="emit('close')" @refresh="emit('refresh')"/>
 <RefundOrders v-else-if="record.business==='refund'" admin detail-only :search="search" @close="emit('close')" @refresh="emit('refresh')"/>
 <Admin v-else route="/admin/orders" :search="search" :user="user" detail-only @close="emit('close')" @refresh="emit('refresh')"/>
</template>
