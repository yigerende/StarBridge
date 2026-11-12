<script setup lang="ts">
import {ChartNoAxesCombined,ChevronDown} from 'lucide-vue-next'
import {usePublicCounts,countText} from '../publicCounts'
const {counts,unavailable}=usePublicCounts()
</script>
<template>
 <details v-if="counts&&!unavailable" class="public-order-counts" @keydown.esc="($el as HTMLDetailsElement).open=false">
  <summary aria-label="查看平台累计订单统计"><ChartNoAxesCombined :size="15"/><span class="public-count-label">累计订单</span><b>{{countText(counts.total)}}</b><ChevronDown :size="12"/></summary>
  <div class="public-count-popover"><header>平台累计订单<small>笔</small></header><div><span><i class="registration"/>注册</span><b>{{countText(counts.registration)}}</b></div><div><span><i class="subscription"/>订阅</span><b>{{countText(counts.subscription)}}</b></div><div><span><i class="refund"/>代退</span><b>{{countText(counts.refund)}}</b></div></div>
 </details>
</template>
<style scoped>
.public-order-counts{position:relative;flex-shrink:0}.public-order-counts summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:7px;padding:8px 10px;border:1px solid #dcebe5;border-radius:9px;background:linear-gradient(120deg,#f4faf7,#f9fbfb);color:#648278;font-size:11px;white-space:nowrap}.public-order-counts summary::-webkit-details-marker{display:none}.public-order-counts summary:focus-visible{outline:2px solid #20816e;outline-offset:3px}.public-order-counts summary b{font-size:13px;color:#1e6252;font-variant-numeric:tabular-nums}.public-count-popover{position:absolute;right:0;top:calc(100% + 12px);width:230px;max-width:calc(100vw - 32px);background:#fff;border:1px solid #e3ebe6;border-radius:14px;box-shadow:0 12px 36px #12382e20;padding:18px;color:#4f6c61;z-index:60}.public-count-popover header{display:flex;justify-content:space-between;font-size:12px;border-bottom:1px solid #edf1ef;padding-bottom:12px;margin-bottom:7px}.public-count-popover header small{color:#8f9d97}.public-count-popover>div{display:flex;align-items:center;justify-content:space-between;padding:9px 0;font-size:12px}.public-count-popover>div span{display:flex;align-items:center;gap:8px}.public-count-popover b{font-variant-numeric:tabular-nums;color:#214b40}.public-count-popover i{width:6px;height:6px;border-radius:50%;background:#65b5a0}.public-count-popover .subscription{background:#7b9dd3}.public-count-popover .refund{background:#ba9bce}@media(max-width:1200px){.public-count-label{display:none}}@media(max-width:700px){.public-order-counts summary{padding:7px;gap:5px}.public-order-counts summary b{font-size:11px}.public-order-counts summary>svg:last-child{display:none}.public-count-popover{position:fixed;right:16px;top:62px}}
</style>
