<script setup lang="ts">
import {Check,Clock3,AlertTriangle,LoaderCircle} from 'lucide-vue-next'
import {computed} from 'vue'
import {subscriptionConfirmed} from '../subscriptionStatus'
const props=defineProps<{status:string;paymentState?:string;workflow?:string;renewal?:string;activation?:string;progressStage?:string}>()
const confirmed=computed(()=>subscriptionConfirmed(props,{state:props.paymentState}))
const separateRenewal=computed(()=>confirmed.value&&!['disabled','kept'].includes(props.renewal||''))
const steps=computed(()=>props.workflow==='subscription'?['提交申请','排队准备','办理准备','订阅付款','订阅生效','取消自动续费',confirmed.value?'订阅成功':'办理完成']:props.paymentState?['提交方案','排队准备','创建结账','付款处理','结果确认']:['提交方案','排队准备','创建结账','链接已生成'])
const attention=computed(()=>['failed','review'].includes(props.status)||props.workflow==='subscription'&&(props.activation==='review'||props.renewal==='review'))
const running=computed(()=>!attention.value&&props.status!=='cancelled'&&(props.status==='processing'||props.status==='succeeded'&&current()<steps.value.length))
const current=()=>{
 if(props.workflow==='subscription'){
  if(confirmed.value)return 7
  if(props.paymentState==='succeeded'&&['pending','review'].includes(props.activation||''))return 4
  if(props.status==='succeeded'){if(props.paymentState&&props.paymentState!=='succeeded')return 3;return props.activation==='active'?5:4}
  if(props.status==='cancelled')return -1
  if(props.status==='queued')return 1
  if(props.paymentState==='succeeded')return props.activation==='active'?5:4
  return props.progressStage==='payment'||props.paymentState&&props.paymentState!=='preparing'?3:2
 }
 return props.paymentState?({preparing:3,quoted:3,submitting:3,checking:4,action_required:4,review:4,succeeded:5,failed:3,cancelled:3}[props.paymentState]??3):({draft:0,queued:1,processing:2,awaiting_payment:4,succeeded:4,failed:2,review:2,cancelled:-1}[props.status]??0)
}
const stepWarning=(i:number)=>i===5&&separateRenewal.value&&props.renewal==='review'||attention.value&&i===current()
const stepDone=(i:number)=>i<current()&&!(i===5&&separateRenewal.value)
const stepRunning=(i:number)=>i===5&&separateRenewal.value&&['pending','retrying'].includes(props.renewal||'')||i===current()&&running.value
</script>
<template><div class="progress-track" aria-label="办理进度" :style="{gridTemplateColumns:`repeat(${steps.length}, minmax(0, 1fr))`}"><div v-for="(s,i) in steps" :key="s" class="progress-step" :aria-current="i===current()?'step':undefined" :class="{done:stepDone(i),active:stepRunning(i)||i===current()&&!attention,attention:stepWarning(i)}"><span><Clock3 v-if="i===5&&separateRenewal&&renewal==='review'" :size="15"/><AlertTriangle v-else-if="stepWarning(i)" :size="15"/><Check v-else-if="stepDone(i)" :size="15"/><LoaderCircle v-else-if="stepRunning(i)" class="spinning" :size="15"/><Clock3 v-else-if="i===current()" :size="15"/><template v-else>{{i+1}}</template></span><b>{{s}}<small v-if="workflow==='subscription'&&i===5&&renewal==='kept'">已选择保留</small><small v-else-if="i===5&&separateRenewal&&renewal==='review'">待确认</small><small v-else-if="stepWarning(i)">{{activation==='review'?'生效待核实':'取消待核实'}}</small></b></div></div></template>
<style scoped>.progress-step small{display:block;margin-top:4px;font-size:10px;font-weight:normal}.progress-step.attention{color:#b45309}.progress-step.attention>span{background:#fff4df;border-color:#eab75a;color:#b45309}@media(max-width:760px){.progress-step b{font-size:10px;line-height:1.6}}</style>
