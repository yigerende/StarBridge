<script setup lang="ts">
import type {Row} from '../lib'
defineProps<{mode:string;tiers:Row[]}>()
const model=defineModel<Row>({required:true})
</script>
<template>
 <div class="batch-accounts">
  <p class="batch-scope">已选 <b>{{model.ids.length}}</b> 个账户</p>
  <template v-if="mode==='accountPricing'">
   <p class="muted small">只修改选中的项目。“清除专属价”恢复使用服务定价和会员规则。</p>
   <section v-for="group in model.groups" :key="group.name">
    <h3>{{group.name}}</h3>
    <div v-for="field in group.fields" :key="field.key" class="batch-price-row">
     <span>{{field.name}}</span>
     <label><span class="sr-only">{{field.name}}调整方式</span><select v-model="field.action" :aria-label="field.name+'调整方式'"><option value="keep">保持原设置</option><option value="set">设置专属价</option><option value="clear">清除专属价</option></select></label>
     <label v-if="field.action==='set'" class="batch-amount"><span class="sr-only">{{field.name}}专属价格</span><input v-model.number="field.amount" :aria-label="field.name+'专属价格'" type="number" min="0" max="1000000" step="0.01" required/><small>积分</small></label>
    </div>
   </section>
  </template>
  <template v-else>
   <label>会员等级<select v-model="model.membership_code" required><option value="__choose__" disabled>请选择</option><option value="">恢复自动等级</option><option v-for="tier in tiers" :key="tier.code" :value="tier.code">{{tier.name}}</option></select></label>
   <label v-if="model.membership_code&&model.membership_code!=='__choose__'">到期时间<input v-model="model.membership_until" type="datetime-local"/><small>留空表示长期有效。</small></label>
   <p v-if="model.membership_code===''" class="muted small">按每个账户的累计消费重新计算会员等级。</p>
  </template>
 </div>
</template>
<style scoped>
.batch-accounts{display:grid;gap:18px}.batch-scope{padding:12px 15px;background:#f0f6f3;border-radius:8px;color:var(--green)}.batch-accounts>p{margin:0;line-height:1.7}.batch-accounts section{padding:16px;border:1px solid var(--line);border-radius:10px}.batch-accounts h3{font-size:14px;margin:0 0 15px}.batch-price-row{display:grid;grid-template-columns:minmax(100px,1fr) 145px 130px;align-items:center;gap:12px;margin-top:12px;font-size:12px}.batch-price-row label{margin:0}.batch-price-row select,.batch-price-row input{font-size:12px}.batch-amount{position:relative}.batch-amount input{padding-right:40px}.batch-amount small{position:absolute;right:9px;top:13px;font-size:11px;color:var(--muted)}
@media(max-width:600px){.batch-price-row{grid-template-columns:1fr 1fr}.batch-price-row>span{grid-column:1/-1}.batch-price-row select,.batch-price-row input{width:100%;min-width:0}.batch-accounts section{padding:13px}}
</style>
