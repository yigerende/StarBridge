<script setup lang="ts">
import {ArrowUpRight,PackageOpen} from 'lucide-vue-next'
import {date,type Row} from '../lib'
import {dashboardStatus} from '../dashboard'
defineProps<{rows:Row[];admin?:boolean;filtered?:boolean}>()
</script>
<template><div class="dash-orders">
 <div v-if="!rows.length" class="dash-empty"><PackageOpen :size="28"/><strong>{{filtered?'暂时没有需要关注的订单':'还没有订单记录'}}</strong><p>{{filtered?'付款核实和注册异常会显示在这里。':'开始一笔办理后，可在这里跟进订阅与注册进度。'}}</p></div>
 <article v-for="row in rows" :key="row.business+row.id" class="dash-order">
  <div class="dash-order-main"><div class="dash-order-title"><span class="dash-business" :class="row.business">{{row.business==='registration'?'注册':'订阅'}}</span><b>{{row.title}}</b></div><small>{{admin?(row.owner+' · '):''}}{{row.email||row.id}}</small><time>{{date(row.created_at)}}</time></div>
  <div class="dash-order-state"><span class="badge" :class="row.attention?'review':row.completed?'succeeded':row.status">{{dashboardStatus(row)}}</span><template v-if="row.business==='registration'"><small>已交付 {{row.delivered}} / {{row.quantity}} 个</small><progress :value="row.delivered" :max="row.quantity" :aria-label="`已交付 ${row.delivered} / ${row.quantity} 个`"/></template><small v-else-if="row.status==='review'" class="dash-warning">请勿再次付款</small></div>
  <div class="dash-order-actions"><a v-if="row.claim_href" :href="row.claim_href" class="text-btn">领取账号<ArrowUpRight :size="14"/></a><a :href="row.href" class="text-btn">{{row.business==='registration'&&!row.completed?'查看进度':'查看详情'}}<ArrowUpRight :size="14"/></a></div>
 </article>
</div></template>
