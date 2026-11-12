<script setup lang="ts">
import {onBeforeUnmount,ref,watch} from 'vue'
import {countText} from '../publicCounts'
const props=defineProps<{value?:number|null;animate?:boolean}>()
const shown=ref<number|null>(null)
let frame=0
watch(()=>[props.value,props.animate] as const,([value,animate])=>{
 cancelAnimationFrame(frame)
 if(value==null){shown.value=null;return}
 if(!animate||matchMedia('(prefers-reduced-motion: reduce)').matches){shown.value=value;return}
 const from=shown.value??0,start=performance.now()
 const tick=(now:number)=>{const t=Math.min(1,(now-start)/900);shown.value=Math.round(from+(value-from)*(1-Math.pow(1-t,3)));if(t<1)frame=requestAnimationFrame(tick)}
 frame=requestAnimationFrame(tick)
},{immediate:true})
onBeforeUnmount(()=>cancelAnimationFrame(frame))
</script>
<template><span :aria-label="countText(value)"><span aria-hidden="true">{{countText(shown)}}</span></span></template>
