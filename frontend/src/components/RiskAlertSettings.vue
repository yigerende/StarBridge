<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {Save} from 'lucide-vue-next'
import {notify,type Row} from '../lib'
import {riskApi} from '../riskApi'
import '../risk-settings.css'
const emit=defineEmits<{saved:[]}>()
const form=ref<Row|null>(null),busy=ref(false),error=ref('')
const groups=[
 {name:'记录与检查',hint:'定时检查只生成告警。',fields:[['retention_days','记录保留天数',1,365],['refresh_seconds','页面刷新间隔（秒）',5,300],['scan_seconds','检查间隔（秒）',10,3600],['behavior_window_seconds','行为统计窗口（秒）',60,86400]]},
 {name:'订单与交付',hint:'用于注册订单的排队、执行和交付检查。',fields:[['duplicate_count','重复提交冲突次数',1,10000],['cancel_count','取消订单次数',1,10000],['failure_min_count','失败率最少统计账号数',1,10000],['failure_percent','失败比例（%）',1,100],['queue_count','单客户排队账号数',1,100000],['queue_seconds','排队超时（秒）',60,604800],['processing_seconds','执行超时（秒）',60,604800]]},
 {name:'访问与积分',hint:'检查已记录的登录行为及积分调整。',fields:[['login_source_accounts','同 IP 尝试登录账号数',2,10000],['large_adjustment','大额积分调整阈值',1,100000000]]}
]
async function load(){error.value='';try{form.value=(await riskApi('/settings')).settings}catch(e){error.value=(e as Error).message}}
async function save(){if(!form.value||busy.value)return;busy.value=true;error.value='';try{await riskApi('/settings','PUT',form.value);notify('告警设置已保存');emit('saved')}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
onMounted(load)
</script>
<template><form class="risk-settings" @submit.prevent="save"><div v-if="error" class="alert danger" role="alert">{{error}}<button v-if="!form" type="button" class="text-btn" @click="load">重试</button></div><fieldset v-if="form" :disabled="busy"><label class="risk-setting-toggle"><span><b>记录业务告警</b><small>关闭后仍可查看历史记录</small></span><input v-model="form.enabled" class="toggle" type="checkbox"/></label><section v-for="group in groups" :key="group.name" class="risk-settings-section"><h3>{{group.name}}</h3><p>{{group.hint}}</p><div class="risk-settings-grid"><label v-for="[key,name,min,max] in group.fields" :key="key">{{name}}<input v-model.number="form[key]" type="number" :min="min" :max="max" required/></label></div></section><label class="risk-setting-toggle"><span><b>积分一致性检查</b><small>核对冻结、交付结算与账本记录</small></span><input v-model="form.finance_checks" class="toggle" type="checkbox"/></label><div class="form-footer"><button class="btn primary" :disabled="busy"><Save :size="16"/>{{busy?'正在保存…':'保存告警设置'}}</button></div></fieldset><p v-else-if="!error" class="muted">正在加载设置…</p></form></template>
