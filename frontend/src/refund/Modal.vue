<script setup lang="ts">
import {onMounted,onUnmounted,ref} from 'vue'
import {X} from 'lucide-vue-next'
const props=defineProps<{title:string;busy?:boolean;wide?:boolean}>(),emit=defineEmits<{close:[]}>()
const el=ref<HTMLElement>(),previous=document.activeElement as HTMLElement|null,overflow=document.body.style.overflow
function key(e:KeyboardEvent){if(e.key==='Escape'&&!props.busy)emit('close');if(e.key==='Tab'){const nodes=Array.from(el.value?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea,select,a[href],[tabindex="0"]')||[]).filter(n=>n.getClientRects().length),first=nodes[0],last=nodes.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus()}}}
onMounted(()=>{document.body.style.overflow='hidden';el.value?.focus();document.addEventListener('keydown',key)})
onUnmounted(()=>{document.body.style.overflow=overflow;document.removeEventListener('keydown',key);previous?.focus()})
</script>
<template><Teleport to="body"><div class="refund-overlay" @mousedown.self="!busy&&emit('close')"><section ref="el" role="dialog" aria-modal="true" aria-labelledby="refund-dialog-title" tabindex="-1" class="refund-dialog" :class="{wide}"><header><h2 id="refund-dialog-title">{{title}}</h2><button class="icon-btn" :disabled="busy" aria-label="关闭" @click="emit('close')"><X :size="20"/></button></header><div class="refund-dialog-body"><slot/></div></section></div></Teleport></template>
<style scoped>
.refund-overlay{position:fixed;inset:0;background:#12283b66;backdrop-filter:blur(3px);z-index:100;display:flex;align-items:center;justify-content:center;padding:20px}.refund-dialog{background:var(--surface,#fff);border:1px solid #ffffff80;border-radius:18px;width:min(820px,100%);max-height:90dvh;overflow:hidden;outline:none;display:flex;flex-direction:column;box-shadow:0 24px 80px #10253626}.refund-dialog.wide{width:min(920px,100%)}.refund-dialog>header{flex:none;display:flex;align-items:center;justify-content:space-between;gap:16px;padding:21px 26px;border-bottom:1px solid var(--line)}.refund-dialog h2{font-size:17px;margin:0}.refund-dialog-body{overflow:auto;padding:24px 26px;overscroll-behavior:contain}@media(max-width:600px){.refund-overlay{padding:10px}.refund-dialog{max-height:94dvh;border-radius:14px}.refund-dialog>header{padding:16px 18px}.refund-dialog-body{padding:18px}.refund-dialog h2{font-size:16px}}
</style>
