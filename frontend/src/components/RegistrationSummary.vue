<script setup lang="ts">
import {cash,type Row} from '../lib'
defineProps<{summary?:Row;admin?:boolean}>()
</script>
<template><section v-if="summary" class="panel registration-summary">
 <div class="panel-heading"><h2>注册服务</h2><span class="pill">交付成功后结算</span></div>
 <div class="stats-grid four">
  <article class="stat"><div class="stat-label">注册订单</div><strong>{{summary.orders||0}}<small>笔</small></strong></article>
  <article class="stat"><div class="stat-label">待完成</div><strong>{{summary.pending||0}}<small>笔</small></strong></article>
  <article class="stat"><div class="stat-label">已交付账号</div><strong>{{summary.delivered||0}}<small>个</small></strong></article>
  <article class="stat"><div class="stat-label">已结算费用</div><strong>{{cash(summary.spent||0)}}<small>积分</small></strong></article>
 </div>
 <p class="muted">订阅与注册共用账户余额，订单和执行队列分别处理。</p>
 <a v-if="!admin" href="#/register" class="btn secondary">注册办理</a>
 <a v-else href="/registration/admin/engine" target="_blank" rel="noopener noreferrer" class="btn secondary">打开原注册管理台</a>
</section></template>
<style scoped>.registration-summary{padding:24px;margin:24px 0}.registration-summary .stat{box-shadow:none}.registration-summary p{margin:12px 0}</style>
