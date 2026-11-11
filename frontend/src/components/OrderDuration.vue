<script setup lang="ts">
import {duration} from '../duration'
import type {Row} from '../lib'
defineProps<{order:Row;subscription?:boolean}>()
</script>
<template><div class="order-duration">
 <strong v-if="subscription" title="开始执行至确认付款成功，不含排队时间">{{order.timing?.payment_seconds!=null?duration(order.timing.payment_seconds):order.status==='queued'?'未开始':order.payment?.state==='succeeded'?'未记录':'未付款'}}<small>付款耗时</small></strong>
 <span title="提交至本次处理结束，包含排队与重试时间；进行中的订单显示已用时间">{{duration(order.timing?.total_seconds)}}<small>{{order.timing?.ongoing?'已用':'总耗时'}}</small></span>
</div></template>
<style scoped>.order-duration{display:grid;gap:7px;white-space:nowrap;font-variant-numeric:tabular-nums}.order-duration strong{font-size:12px;font-weight:600}.order-duration span{font-size:12px}.order-duration small{display:inline!important;margin-left:7px!important;font-size:10px;color:var(--muted);font-weight:400}</style>
