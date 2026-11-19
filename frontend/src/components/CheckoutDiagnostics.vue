<script setup lang="ts">
import {computed, onBeforeUnmount, ref, watch} from 'vue'
import {api, type Row} from '../lib'
const props=defineProps<{orderId:string;events:Row[]}>()
const rounds=ref<Row[]>([]), error=ref(''), loading=ref(false)
let controller:AbortController|undefined
const steps:Record<string,string>={homepage:'访问首页',session_refresh:'刷新登录态',account_check:'查询账号',subscription_check:'查询订阅',verification:'请求验证',checkout_create:'创建结账链接',checkout_price:'查询结账金额',other:'上游请求'}
const points:Record<string,string>={before_request:'请求前',after_response:'响应后',refresh_return:'刷新结束',wire_credentials:'实际请求',input_credentials:'本轮输入',session_refresh:'刷新结果'}
const failures=computed(()=>{
 const detailed=rounds.value.flatMap(r=>r.events.filter((e:Row)=>e.event==='http_request'&&(e.status>=400||e.outcome==='failed')&&!(e.step==='subscription_check'&&e.status===404)||e.event==='session_refresh'&&e.outcome==='failed').map((e:Row)=>({...e,round:r.round})))
 if(detailed.length)return detailed
 return props.events.filter(e=>e.source==='technical'&&e.phase==='checkout'&&e.state==='failed').map(e=>({round:e.round,step:'checkout_create',status:e.http_status,upstream_code:e.diagnostics?.response_error_code,upstream_message:e.description,legacy:true}))
})
async function load(){
 controller?.abort();const request=new AbortController();controller=request
 loading.value=true;error.value=''
 try{const data=await api(`/admin/orders/${encodeURIComponent(props.orderId)}/checkout-diagnostics`,'GET',undefined,{},request.signal);if(controller===request)rounds.value=data.rounds||[]}
 catch(e){if(controller===request&&!request.signal.aborted)error.value='诊断暂时无法读取，可稍后刷新。'}
 finally{if(controller===request)loading.value=false}
}
watch(()=>props.orderId,()=>{rounds.value=[];void load()},{immediate:true})
onBeforeUnmount(()=>controller?.abort())
</script>
<template>
<section class="checkout-diagnostics" aria-label="提链异常记录" v-if="failures.length||rounds.length||error">
 <header><h4>提链异常记录</h4><button class="text-btn" :disabled="loading" @click="load">{{loading?'读取中…':'刷新记录'}}</button></header>
 <p v-if="error" role="status">{{error}}</p>
 <ol v-if="failures.length"><li v-for="(e,i) in failures" :key="i"><b>{{i===0?'首次异常':'后续异常'}} · 第 {{e.round||'—'}} 轮 · {{e.legacy?'生成结账链接':steps[e.step]||e.step}}</b><span v-if="e.status">HTTP {{e.status}}</span><p>{{e.upstream_message||e.upstream_detail||e.upstream_title||e.upstream_summary||e.response_error||e.upstream_code||e.errorCode||'未保留具体原因'}}</p><small v-if="e.upstream_code">错误码：{{e.upstream_code}}<template v-if="e.upstream_type"> · {{e.upstream_type}}</template></small><small v-if="e.upstream_request_id">请求标识：{{e.upstream_request_id}}</small></li></ol>
 <p v-else>已记录的轮次没有提链异常。</p>
 <details v-if="rounds.length"><summary>查看凭据版本变化</summary><div v-for="r in rounds" :key="r.round" class="round"><b>第 {{r.round}} 轮</b><div v-for="(e,i) in r.events.filter((x:Row)=>['input_credentials','credentials','wire_credentials','session_refresh'].includes(x.event))" :key="i" class="version"><span>{{steps[e.step]||'本轮输入'}} · {{points[e.point||e.event]||e.event}}</span><code>AT {{e.at_version||'未携带'}}<br>Session {{e.session_version||'—'}}<br>Cookie {{e.auth_cookie_version||'—'}}<template v-if="e.proxy_version"><br>代理会话 {{e.proxy_version}}</template></code><small v-if="e.duplicate_auth_cookies">请求含同名登录 Cookie</small></div></div></details>
</section>
</template>
<style scoped>
.checkout-diagnostics{border:1px solid #e4ebef;border-radius:10px;margin:18px 0;padding:16px;font-size:12px}.checkout-diagnostics header{display:flex;align-items:center;justify-content:space-between;gap:12px}.checkout-diagnostics h4{margin:0;font-size:13px}.checkout-diagnostics ol{padding-left:20px;margin-bottom:0}.checkout-diagnostics li{padding:12px 0;border-bottom:1px solid #edf1f4;overflow-wrap:anywhere}.checkout-diagnostics li:last-child{border:0}.checkout-diagnostics li>span{margin-left:10px;color:#996017}.checkout-diagnostics p{line-height:1.7;margin:8px 0}.checkout-diagnostics small{display:block;color:#728792;line-height:1.8}.checkout-diagnostics summary{cursor:pointer;padding-top:14px}.round{margin-top:16px}.version{display:grid;grid-template-columns:1fr 1fr;gap:8px;border-bottom:1px solid #edf1f4;padding:10px 0}.version code{font-size:11px;line-height:1.7;overflow-wrap:anywhere}.version small{grid-column:1/-1}@media(max-width:650px){.version{grid-template-columns:1fr}}
</style>
