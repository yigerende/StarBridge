<script setup lang="ts">
defineProps<{modelValue:'subscription'|'registration'}>()
const emit=defineEmits<{'update:modelValue':[value:'subscription'|'registration']}>()
</script>
<template><div class="business-tabs" role="tablist" aria-label="服务类型">
 <button role="tab" :aria-selected="modelValue==='subscription'" :class="{active:modelValue==='subscription'}" @click="emit('update:modelValue','subscription')">订阅</button>
 <button role="tab" :aria-selected="modelValue==='registration'" :class="{active:modelValue==='registration'}" @click="emit('update:modelValue','registration')">注册</button>
</div></template>
<style scoped>
.business-tabs{display:flex;gap:5px;padding:5px;margin:0 0 22px;width:fit-content;background:var(--surface-muted,#eef1ef);border:1px solid var(--line,#dce3de);border-radius:12px}
button{border:0;background:transparent;color:inherit;padding:10px 30px;border-radius:8px;font:inherit;font-weight:600;cursor:pointer}
button.active{background:var(--surface,#fff);color:var(--text,#17352b);box-shadow:0 1px 4px #142f2014}
button:focus-visible{outline:2px solid #27664c;outline-offset:2px}
</style>
