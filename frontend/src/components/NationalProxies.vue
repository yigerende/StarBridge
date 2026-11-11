<script setup lang="ts">
import {onMounted,onUnmounted,ref} from 'vue'
const host=ref<HTMLElement>(),error=ref('')
let disposed=false,unmount:(()=>void)|undefined
onMounted(async()=>{try{const {mount}=await import('../subscription-proxies/mount');if(!disposed&&host.value)unmount=mount(host.value)}catch(e){error.value=(e as Error).message}})
onUnmounted(()=>{disposed=true;unmount?.()})
</script>
<template><div v-if="error" class="alert danger">{{error}}</div><section ref="host" class="national-proxies" aria-label="订阅国家代理"/></template>
