<script setup lang="ts">
import {onMounted,onUnmounted,ref} from 'vue'
import {X} from 'lucide-vue-next'
const props=defineProps<{title:string;busy?:boolean}>(),emit=defineEmits<{close:[]}>()
const el=ref<HTMLElement>(),previous=document.activeElement as HTMLElement|null,overflow=document.body.style.overflow
function key(e:KeyboardEvent){if(e.key==='Escape'&&!props.busy)emit('close');if(e.key==='Tab'){const nodes=Array.from(el.value?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea,select,a[href],[tabindex="0"]')||[]).filter(n=>n.getClientRects().length),first=nodes[0],last=nodes.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus()}}}
onMounted(()=>{document.body.style.overflow='hidden';el.value?.focus();document.addEventListener('keydown',key)})
onUnmounted(()=>{document.body.style.overflow=overflow;document.removeEventListener('keydown',key);previous?.focus()})
</script>
<template><Teleport to="body"><div class="refund-overlay" @mousedown.self="!busy&&emit('close')"><section ref="el" role="dialog" aria-modal="true" aria-labelledby="refund-dialog-title" tabindex="-1" class="refund-dialog"><header><h2 id="refund-dialog-title">{{title}}</h2><button class="icon-btn" :disabled="busy" aria-label="关闭" @click="emit('close')"><X :size="20"/></button></header><slot/></section></div></Teleport></template>
<style scoped>.refund-overlay{position:fixed;inset:0;background:#10251e77;z-index:100;display:flex;align-items:center;justify-content:center;padding:20px}.refund-dialog{background:var(--surface,#fff);border-radius:18px;width:min(820px,100%);max-height:90dvh;overflow:auto;padding:26px;outline:none}.refund-dialog header{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:24px}.refund-dialog h2{font-size:19px;margin:0}@media(max-width:600px){.refund-overlay{padding:10px}.refund-dialog{padding:18px}}</style>
