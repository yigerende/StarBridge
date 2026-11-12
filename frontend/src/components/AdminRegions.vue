<script setup lang="ts">
import {ref,computed,onMounted,watch} from 'vue'
import {Plus,Search,RefreshCw,Pencil,Trash2} from 'lucide-vue-next'
import {api,notify,type Row} from '../lib'
import {useRemoteList} from '../useRemoteList'
import ListPager from './ListPager.vue'
import SortHeader from './SortHeader.vue'
import Modal from './Modal.vue'
import RegionPriceSchedule from './RegionPriceSchedule.vue'
const emit=defineEmits<{'changed':[]}>()
const search=ref(''),status=ref(''),busy=ref(false),error=ref(''),modal=ref(''),editing=ref(''),form=ref<Row>({})
const selected=ref<string[]>([]),currencies=ref<string[]>([]),mode=ref('manual'),batchResult=ref<Row[]>([])
const batchScope=ref('all'),batchSummary=ref('')
const {page,size,sortBy,sortDir,data,rows:visible,loading,error:listError,load:loadPage,sort}=useRemoteList('admin-regions',()=>'/admin/regions',{q:search,status},{sortBy:'sort',sortDir:'asc'})
const rows=computed<Row[]>(()=>data.value.all_index||[])
const enabledCount=computed(()=>rows.value.filter(r=>r.enabled).length)
const batchRows=computed<Row[]>(()=>batchScope.value==='selected'?rows.value.filter(r=>selected.value.includes(r.code)):batchScope.value==='filtered'?data.value.filtered_index||[]:rows.value)
watch(data,r=>{currencies.value=r.currencies||[];mode.value=r.proxy_mode||'manual'})
const enableCount=computed(()=>batchRows.value.filter(r=>!r.enabled).length)
const disableCount=computed(()=>batchRows.value.filter(r=>r.enabled).length)
const scopeLabel=computed(()=>batchScope.value==='selected'?'选中':batchScope.value==='filtered'?'筛选结果':'全部')
watch([search,status],()=>{selected.value=[];batchScope.value=search.value.trim()||status.value?'filtered':'all'})
async function load(){error.value='';await loadPage()}
function edit(row?:Row){editing.value=row?.code||'';form.value=row?{...row}:{code:'',name:'',currency:'',sort:rows.value.length,enabled:true};error.value='';modal.value='edit'}
async function mutate(fn:()=>Promise<unknown>,message='订阅地区已更新'){if(busy.value)return;busy.value=true;error.value='';try{await fn();modal.value='';if(message)notify(message);await load();emit('changed')}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function batch(enabled:boolean){const codes=batchRows.value.filter(r=>r.enabled!==enabled).map(r=>r.code);if(!codes.length)return;await mutate(async()=>{const r=await api('/admin/regions/batch','POST',{codes,enabled});batchResult.value=r.skipped;batchSummary.value=`已${enabled?'启用':'禁用'} ${r.changed} 个地区${r.skipped.length?`，另有 ${r.skipped.length} 个未更新`:''}。`;notify(batchSummary.value)},'')}
function selectVisible(event:Event){selected.value=(event.target as HTMLInputElement).checked?[...new Set([...selected.value,...visible.value.map(r=>r.code)])]:selected.value.filter(code=>!visible.value.some(r=>r.code===code));batchScope.value='selected'}
function save(){return mutate(()=>api('/admin/regions'+(editing.value?'/'+editing.value:''),editing.value?'PUT':'POST',{...form.value,code:form.value.code.trim().toUpperCase(),currency:form.value.currency.trim().toUpperCase()}))}

</script>
<template>
  <div class="filter-strip"><RegionPriceSchedule/><div class="search-box"><Search :size="16"/><input v-model="search" placeholder="搜索国家、代码或币种" aria-label="搜索订阅地区" :disabled="busy"/></div><select v-model="status" aria-label="地区状态" :disabled="busy"><option value="">全部状态</option><option value="enabled">已启用</option><option value="disabled">已禁用</option><option value="missing">币种待配置</option><option value="unavailable">当前代理不支持</option></select><button class="btn secondary" :disabled="loading||busy" @click="load"><RefreshCw :size="16"/>刷新</button><button class="btn primary" :disabled="busy" @click="edit()"><Plus :size="16"/>新增地区</button></div>
  <section class="panel region-batch" aria-label="地区批量启停"><div><b>批量启用 / 禁用</b><p class="muted">共 {{rows.length}} 个地区，已启用 {{enabledCount}} 个，已禁用 {{rows.length-enabledCount}} 个</p></div><div class="batch-controls"><label>操作范围<select v-model="batchScope" aria-label="批量操作范围" :disabled="loading||busy"><option value="all">全部地区（{{rows.length}}）</option><option value="filtered">当前筛选（{{data.total||0}}）</option><option value="selected">已选地区（{{selected.length}}）</option></select></label><div class="actions"><button class="btn primary" :disabled="loading||busy||!enableCount" @click="batch(true)">{{scopeLabel}}启用（{{enableCount}}）</button><button class="btn secondary" :disabled="loading||busy||!disableCount" @click="batch(false)">{{scopeLabel}}禁用（{{disableCount}}）</button></div></div><small class="muted">无需逐个勾选。按钮数量表示本次需要更改的地区数；搜索后默认操作筛选结果。</small></section>
  <p class="callout">各国价格按此目录显示。手动代理、独立会话代理使用已启用地区；国家代理按已拉取国家提供支付选择。切换代理或同步供应商不会修改本目录，已受理订单保留原地区及币种。</p>
  <p v-if="listError" class="alert danger">{{listError}}</p><div v-if="error&&!modal" class="alert danger" role="alert">{{error}}</div>
  <p v-if="batchSummary" class="callout" role="status">{{batchSummary}}</p>
  <div v-if="batchResult.length" class="callout"><b>以下地区未更新</b><p v-for="r in batchResult" :key="r.code">{{r.code}}：{{r.reason}}</p></div>
  <p class="muted">共 {{rows.length}} 个地区 · 当前代理模式：{{mode==='national_pool'?'国家代理':mode==='country_pool'?'独立会话代理':'手动代理'}}。币种来自参考配置，可单独调整；实际扣款以付款页面为准。</p>
  <div class="panel regions-table"><table><thead><tr><th><input type="checkbox" aria-label="选择本页地区" :disabled="loading||busy||!visible.length" :checked="!!visible.length&&visible.every(r=>selected.includes(r.code))" :indeterminate="visible.some(r=>selected.includes(r.code))&&!visible.every(r=>selected.includes(r.code))" @change="selectVisible"/></th><SortHeader label="地区" field="name" :sort-by="sortBy" :sort-dir="sortDir" @sort="sort"/><SortHeader label="国家代码" field="code" :sort-by="sortBy" :sort-dir="sortDir" @sort="sort"/><SortHeader label="结算币种" field="currency" :sort-by="sortBy" :sort-dir="sortDir" @sort="sort"/><th>代理 / 流程</th><SortHeader label="排序" field="sort" :sort-by="sortBy" :sort-dir="sortDir" @sort="sort"/><SortHeader label="状态" field="enabled" :sort-by="sortBy" :sort-dir="sortDir" @sort="sort"/><th>操作</th></tr></thead><tbody>
    <tr v-for="row in visible" :key="row.code"><td><input type="checkbox" v-model="selected" :value="row.code" :disabled="busy" :aria-label="'选择'+row.name" @change="batchScope='selected'"/></td><td>{{row.name}}</td><td class="mono">{{row.code}}</td><td class="mono">{{row.currency||'待配置'}}</td><td><small>{{row.proxy_available?'代理支持':'代理待配置或不支持'}}</small><small>{{row.flow_supported?'当前流程支持':'当前流程不支持'}}</small></td><td>{{row.sort}}</td><td><span class="badge" :class="row.enabled?'active':'disabled'">{{row.enabled?'已启用':'已禁用'}}</span></td><td><div class="actions"><button class="text-btn" :disabled="busy" @click="edit(row)"><Pencil :size="14"/>编辑</button><button class="text-btn" :disabled="busy" @click="mutate(()=>api('/admin/regions/'+row.code,'PUT',{...row,enabled:!row.enabled}))">{{row.enabled?'禁用':'启用'}}</button><button class="text-btn danger-text" :disabled="busy" @click="form={...row};modal='delete';error=''"><Trash2 :size="14"/>删除</button></div></td></tr>
    <tr v-if="!visible.length"><td colspan="8" class="empty">{{loading?'加载中…':'暂无符合条件的地区'}}</td></tr>
  </tbody></table><ListPager v-model:page="page" v-model:size="size" :total="data.total||0" :loading="loading"/></div>
  <Modal v-if="modal" :title="modal==='delete'?'删除订阅地区':editing?'编辑订阅地区':'新增订阅地区'" @close="!busy&&(modal='')">
    <div v-if="error" class="alert danger" role="alert">{{error}}</div>
    <template v-if="modal==='delete'"><p>删除 {{form.name}}（{{form.code}}）后，该地区将从价格目录及手动、独立会话支付列表移除；国家代理支付列表仍由已拉取国家决定。</p><div class="form-footer"><button class="btn secondary" :disabled="busy" @click="modal=''">返回</button><button class="btn primary" :disabled="busy" @click="mutate(()=>api('/admin/regions/'+form.code,'DELETE'))">确认删除</button></div></template>
    <form v-else @submit.prevent="save"><div class="form-grid"><label>地区名称<input v-model="form.name" maxlength="60" required/></label><label>国家代码<input v-model="form.code" maxlength="2" minlength="2" pattern="[A-Za-z]{2}" :readonly="!!editing" placeholder="例如 US" required/><small>使用两位国家代码，创建后固定。</small></label><label>结算币种<select v-model="form.currency" :required="form.enabled"><option value="">待配置</option><option v-for="c in currencies" :key="c" :value="c">{{c}}</option></select></label><label>排序<input v-model.number="form.sort" type="number" min="-10000" max="10000" required/><small>越小越靠前。</small></label></div><label class="checkbox-label"><input v-model="form.enabled" type="checkbox"/>启用该地区</label><div class="form-footer"><button class="btn primary" :disabled="busy">{{busy?'保存中…':'保存地区'}}</button></div></form>
  </Modal>
</template>
<style scoped>.region-batch{padding:20px;margin-bottom:16px;display:flex;align-items:center;justify-content:space-between;gap:18px;flex-wrap:wrap}.region-batch p{margin:6px 0 0;font-size:13px}.region-batch>small{flex-basis:100%}.batch-controls{display:flex;align-items:end;gap:14px;flex-wrap:wrap}.batch-controls label{margin:0;min-width:180px}.batch-controls .actions{flex-wrap:wrap}.regions-table{overflow-x:auto}.regions-table table{width:100%;border-collapse:collapse;min-width:650px}.regions-table th,.regions-table td{padding:16px;text-align:left;border-bottom:1px solid var(--line,#e7ebe8)}.regions-table th{font-size:12px;color:var(--muted,#66756e)}.regions-table .actions{white-space:nowrap}@media(max-width:600px){.batch-controls,.batch-controls label{width:100%}.batch-controls .actions{width:100%;display:grid;grid-template-columns:1fr 1fr}.batch-controls .btn{padding:10px 8px;font-size:12px}}</style>
