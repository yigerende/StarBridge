<script setup lang="ts">
import {ref,watch} from 'vue'
import {api,date} from '../lib'
import {useRemoteList} from '../useRemoteList'
import ListPager from './ListPager.vue'
import SortHeader from './SortHeader.vue'
import Modal from '../refund/Modal.vue'
const props=defineProps<{active:boolean;revision:number}>()
const query=ref(''),status=ref(''),clearing=ref(false),selected=ref(''),actionError=ref('')
const list=useRemoteList('admin-refund-account-restrictions',()=>props.active?'/admin/refund/account-restrictions':'',{q:query,status},{sortBy:'first_seen_at',sortDir:'desc'})
const {page,size,sortBy,sortDir,data,rows,loading,error}=list
const states:Record<string,string>={blocked:'已限制提交',allowed:'仍可再次提交',cleared:'已解除'}
watch(()=>props.revision,()=>{if(props.active)void list.load()})
async function clear(){
 clearing.value=true;actionError.value=''
 try{await api('/admin/refund/account-restrictions/'+encodeURIComponent(selected.value)+'/clear','POST');selected.value='';await list.load()}
 catch(e){actionError.value=(e as Error).message}
 finally{clearing.value=false}
}
</script>
<template>
 <section class="account-restrictions">
  <div class="section-title"><div><h3>代退账号提交限制</h3><p class="muted">按代退账号邮箱全站累计，不限制星桥用户。只有最终失败订单计数；成功后清零，单内重试不重复计数。</p></div><button type="button" class="btn secondary" :disabled="loading" @click="list.load">刷新</button></div>
  <p v-if="error" class="error-banner" role="alert">{{error}}</p>
  <div class="table-toolbar"><input v-model="query" type="search" placeholder="搜索账号邮箱" maxlength="200"/><select v-model="status"><option value="">全部状态</option><option value="blocked">已限制提交</option><option value="allowed">仍可再次提交</option><option value="cleared">已解除</option></select></div>
  <div class="table-scroll"><table><thead><tr>
   <SortHeader field="email" label="账号" :sort-by="sortBy" :sort-dir="sortDir" @sort="list.sort"/>
   <th>限制原因</th><SortHeader field="first_seen_at" label="发现时间" :sort-by="sortBy" :sort-dir="sortDir" @sort="list.sort"/>
   <SortHeader field="failure_count" label="失败笔数 / 上限" :sort-by="sortBy" :sort-dir="sortDir" @sort="list.sort"/>
   <th>状态</th><th>操作</th>
  </tr></thead><tbody>
   <tr v-for="r in rows" :key="r.email"><td>{{r.email}}</td><td>{{r.reason_label}}</td><td>{{date(r.first_seen_at)}}<small>最近确认 {{date(r.last_seen_at)}}</small></td><td>{{r.failure_count}} / {{r.failure_limit||'关闭'}}<small v-if="r.reason!=='consecutive_failures'">停用后已提交 {{r.resubmissions}} / {{r.deactivated_resubmit_limit}}</small></td><td><span class="badge" :class="r.status==='blocked'?'red':r.status==='cleared'?'gray':'orange'">{{states[r.status]}}</span><small v-if="r.cleared_at">{{date(r.cleared_at)}}</small></td><td><button v-if="r.status!=='cleared'" type="button" class="btn secondary" @click="selected=r.email;actionError=''">解除限制</button><span v-else class="muted">—</span></td></tr>
   <tr v-if="!rows.length"><td colspan="6" class="empty">{{loading?'正在加载…':'暂无匹配记录'}}</td></tr>
  </tbody></table></div>
  <ListPager v-model:page="page" v-model:size="size" :total="data.total||0" :loading="loading"/>
 </section>
 <Modal v-if="selected" title="解除提交限制" :busy="clearing" @close="!clearing&&(selected='')">
  <p class="account-email">{{selected}}</p><p>确认解除这个代退账号的限制？连续失败笔数和再次提交次数将清零，已有订单继续处理。</p>
  <p v-if="actionError" class="error-banner" role="alert">{{actionError}}</p>
  <div class="modal-actions"><button type="button" class="btn secondary" :disabled="clearing" @click="selected=''">取消</button><button type="button" class="btn primary" :disabled="clearing" @click="clear">{{clearing?'正在解除…':'确认解除'}}</button></div>
 </Modal>
</template>
<style scoped>
.account-restrictions{margin-top:24px;padding-top:24px;border-top:1px solid var(--line);min-width:0}.section-title p{margin:8px 0 16px}.table-scroll{overflow-x:auto}table{width:100%;min-width:820px}td{vertical-align:top;font-size:12px;white-space:nowrap}td small{display:block;color:var(--muted);margin-top:7px}.empty{text-align:center;padding:30px;color:var(--muted)}.account-email{overflow-wrap:anywhere;font-weight:600}.modal-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:20px}
</style>
