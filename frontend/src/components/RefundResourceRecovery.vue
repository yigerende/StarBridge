<script setup lang="ts">
import {computed,onUnmounted,ref} from 'vue'
import {api,date} from '../lib'
import {useRemoteList} from '../useRemoteList'
import ListPager from './ListPager.vue'
import SortHeader from './SortHeader.vue'
const props=defineProps<{active:boolean}>()
const query=ref(''),actionError=ref(''),retrying=ref('')
const list=useRemoteList('admin-refund-resource-recovery',()=>props.active?'/admin/refund/resource-recovery':'',{q:query},{sortBy:'next_at',sortDir:'asc'})
const {page,size,sortBy,sortDir,data,rows,loading,error}=list
const health=computed(()=>data.value.health||[])
const states:Record<string,string>={watching:'复查中',cleaning:'待清理',review:'待核实'}
const reasons:Record<string,string>={mother_capacity:'席位不足',mother_network:'母号网络异常',mother_http:'母号请求异常',mother_preflight_failed:'检查未通过',mother_preflight_auth:'凭据失效',mother_invite_auth:'邀请权限失效',mother_shape:'列表无法确认',mother_pagination:'列表不完整',login_upstream:'登录服务异常'}
async function retry(id:string){retrying.value=id;actionError.value='';try{await api(`/admin/refund/resource-recovery/${id}/retry`,'POST');await list.load()}catch(e){actionError.value=(e as Error).message}finally{retrying.value=''}}
async function probe(scope:string){retrying.value=scope;actionError.value='';try{await api(`/admin/refund/resource-recovery/health/${encodeURIComponent(scope)}/retry`,'POST');await list.load()}catch(e){actionError.value=(e as Error).message}finally{retrying.value=''}}
const timer=setInterval(()=>{if(props.active&&!document.hidden&&!loading.value)void list.load()},10000)
onUnmounted(()=>clearInterval(timer))
</script>
<template>
 <section class="resource-recovery">
  <div class="section-title"><div><h3>资源恢复</h3><p class="muted">订单结束后，未完成的资源清理仍会继续。</p></div><button type="button" class="btn secondary" :disabled="loading" @click="list.load">刷新</button></div>
  <p v-if="error||actionError" class="error-banner" role="alert">{{error||actionError}}</p>
  <div v-for="h in health" :key="h.scope" class="resource-alert"><b>{{h.scope==='login'?'登录服务':h.mother_name||'母号'}} · {{reasons[h.reason]||'资源异常'}}</b><span>下次检查 {{date(h.next_at)}}</span><button type="button" class="btn secondary" :disabled="!!retrying" @click="probe(h.scope)">立即检查</button></div>
  <div class="table-toolbar"><input v-model="query" type="search" placeholder="搜索子号、母号或订单号" maxlength="200"/></div>
  <div class="table-scroll"><table><thead><tr>
   <SortHeader field="email" label="子号 / 母号" :sort-by="sortBy" :sort-dir="sortDir" @sort="list.sort"/>
   <SortHeader field="state" label="状态" :sort-by="sortBy" :sort-dir="sortDir" @sort="list.sort"/>
   <th>处理情况</th><SortHeader field="next_at" label="下次检查" :sort-by="sortBy" :sort-dir="sortDir" @sort="list.sort"/><th>操作</th>
  </tr></thead><tbody>
   <tr v-for="t in rows" :key="t.id"><td><b>{{t.email}}</b><small>{{t.mother_name}}</small></td><td><span class="resource-state" :class="t.state">{{states[t.state]}}</span><small>失败 {{t.attempts}} 次</small></td><td class="resource-message">{{t.message||'等待检查'}}</td><td>{{date(t.next_at)}}</td><td><button type="button" class="btn secondary" :disabled="!!retrying" @click="retry(t.id)">{{retrying===t.id?'正在安排…':'立即检查'}}</button></td></tr>
   <tr v-if="!rows.length"><td colspan="5" class="resource-empty">{{loading?'正在加载…':query?'没有匹配的记录':'暂无待清理资源'}}</td></tr>
  </tbody></table></div>
  <ListPager v-model:page="page" v-model:size="size" :total="data.total||0" :loading="loading"/>
 </section>
</template>
<style scoped>
.resource-recovery{margin-top:24px;padding-top:24px;border-top:1px solid var(--line);min-width:0}.section-title p{margin:8px 0 16px}.resource-alert{display:flex;flex-wrap:wrap;justify-content:space-between;gap:10px;padding:14px 16px;background:var(--surface-alt,#f8f8f5);border:1px solid var(--line);border-radius:8px;margin-bottom:10px;font-size:12px}.resource-alert span{color:var(--muted)}.table-scroll{overflow-x:auto}table{width:100%;min-width:760px}td{vertical-align:top;font-size:12px;white-space:nowrap}td small{display:block;color:var(--muted);margin-top:7px}.resource-message{white-space:normal;min-width:200px;max-width:360px;line-height:1.7}.resource-state{display:inline-block;padding:4px 8px;border-radius:5px;background:#eef3f1;color:#356253}.resource-state.cleaning,.resource-state.review{background:#fff3e3;color:#9a6124}.resource-empty{text-align:center;color:var(--muted);padding:30px}td .btn{font-size:12px;padding:7px 12px}
</style>
