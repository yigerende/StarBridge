<script setup lang="ts">
import {ShieldCheck} from 'lucide-vue-next'
import type {Row} from '../lib'
const form=defineModel<Row>({required:true})
defineProps<{fields:Row[]}>()
const groups=[
 {name:'访问与业务请求',hint:'每个来源 IP 单独计数，单位：次 / 分钟。',keys:['edge_per_minute','api_per_minute','order_per_minute','redeem_per_minute','inspect_per_minute','export_per_minute']},
 {name:'登录与验证',hint:'登录、注册、重置密码和滑块验证，单位：次 / 分钟 / IP。',keys:['login_per_minute','register_per_minute','reset_per_minute','slider_per_minute','slider_verify_per_minute']},
 {name:'邮件验证码',hint:'发送次数按 IP 统计，重发间隔按邮箱计算。',keys:['email_per_minute','email_per_day','email_cooldown_seconds']},
 {name:'账户与记录',hint:'设置订单数量、登录有效期和记录保留时间。',keys:['max_open_orders','session_hours','event_retention_days','max_topup']}
]
function label(f:Row){if(f.k==='email_per_minute')return '每分钟发送验证码';return f.n.replace('每 IP 每分钟','').replace('每 IP 每日','每日')}
</script>
<template><div class="risk-settings"><div class="risk-settings-note"><ShieldCheck :size="18"/><p>网页和 API 使用同一套 IP 限流规则。不同 IP 独立计数，已登录并通过鉴权的管理员不限流。</p></div><section v-for="group in groups" :key="group.name" class="risk-settings-section"><h3>{{group.name}}</h3><p>{{group.hint}}</p><div class="risk-settings-grid"><label v-for="f in fields.filter(f=>group.keys.includes(f.k))" :key="f.k">{{label(f)}}<input v-model.number="form[f.k]" type="number" :min="f.min" :max="f.max" :step="f.money?'0.01':'1'" required/></label></div></section></div></template>
