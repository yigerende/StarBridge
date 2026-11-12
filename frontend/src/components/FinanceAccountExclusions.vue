<script setup lang="ts">
import {computed,onUnmounted,ref,watch} from 'vue'
import {ListFilter,X,ChevronLeft,ChevronRight} from 'lucide-vue-next'
import {api,type Row} from '../lib'
const props=defineProps<{includeAdmin:boolean}>()
const selected=defineModel<Row[]>({default:()=>[]})
const opened=ref(false),search=ref(''),page=ref(1),items=ref<Row[]>([]),total=ref(0),loading=ref(false),error=ref('')
const ids=computed(()=>new Set(selected.value.map(x=>x.id)))
let controller:AbortController|undefined,timer:ReturnType<typeof setTimeout>|undefined
async function load(){
 if(!opened.value)return
 controller?.abort();const request=new AbortController();controller=request;loading.value=true;error.value=''
 try{const data=await api('/admin/finance/reports/options?'+new URLSearchParams({q:search.value,include_admin:String(props.includeAdmin),page:String(page.value)}),'GET',undefined,{},request.signal);if(controller===request){items.value=data.items;total.value=data.total}}
 catch(e){if(controller===request&&(e as Error).name!=='AbortError')error.value=(e as Error).message}
 finally{if(controller===request)loading.value=false}
}
function toggle(item:Row){selected.value=ids.value.has(item.id)?selected.value.filter(x=>x.id!==item.id):[...selected.value,{id:item.id,name:item.name,email:item.email}].slice(0,200)}
watch(search,()=>{clearTimeout(timer);timer=setTimeout(()=>{page.value=1;void load()},250)})
watch([opened,page,()=>props.includeAdmin],load)
onUnmounted(()=>{controller?.abort();clearTimeout(timer)})
</script>
<template><details class="finance-exclusions" @toggle="opened=($event.target as HTMLDetailsElement).open" @keydown.esc="($event.currentTarget as HTMLDetailsElement).open=false">
 <summary><ListFilter :size="15"/>{{selected.length?`已排除 ${selected.length} 个账号`:'排除账号'}}</summary>
 <div class="exclusions-popover"><header><b>不计入报表的账号</b><button type="button" class="text-btn" :disabled="!selected.length" @click="selected=[]">清空</button></header>
  <input v-model="search" aria-label="搜索排除账号" placeholder="搜索称呼、邮箱或账户 ID" maxlength="200"/>
  <div v-if="selected.length" class="excluded-tags"><button v-for="item in selected" :key="item.id" type="button" @click="toggle(item)" :aria-label="`取消排除 ${item.email}`">{{item.name||item.email}}<X :size="12"/></button></div>
  <p v-if="error" role="alert">{{error}}<button type="button" @click="load">重试</button></p><p v-else-if="loading" role="status">正在加载…</p>
  <div v-else class="exclusion-options"><label v-for="item in items" :key="item.id"><input type="checkbox" :checked="ids.has(item.id)" :disabled="!ids.has(item.id)&&selected.length>=200" @change="toggle(item)"/><span><b>{{item.name||'未填写称呼'}}</b><small>{{item.email}}</small></span></label><p v-if="!items.length">没有匹配的账号</p></div>
  <footer><span>已选 {{selected.length}} / 200</span><div v-if="total>50"><button type="button" aria-label="上一页排除账号" :disabled="loading||page===1" @click="page--"><ChevronLeft :size="16"/></button>{{page}} / {{Math.ceil(total/50)}}<button type="button" aria-label="下一页排除账号" :disabled="loading||page*50>=total" @click="page++"><ChevronRight :size="16"/></button></div></footer>
 </div></details></template>
<style scoped>
.finance-exclusions{position:relative}.finance-exclusions summary{display:flex;align-items:center;gap:7px;list-style:none;cursor:pointer;padding:9px 12px;border:1px solid var(--line);border-radius:8px;background:white;font-size:12px;white-space:nowrap}.finance-exclusions summary::-webkit-details-marker{display:none}.exclusions-popover{position:absolute;right:0;top:calc(100% + 8px);z-index:40;width:340px;max-width:calc(100vw - 48px);padding:16px;background:white;border:1px solid var(--line);border-radius:12px;box-shadow:0 12px 35px #18332a20}.exclusions-popover header,.exclusions-popover footer,.exclusions-popover footer>div{display:flex;justify-content:space-between;align-items:center;gap:10px}.exclusions-popover header{margin-bottom:12px;font-size:13px}.exclusions-popover>input{width:100%;font-size:12px}.exclusion-options{max-height:240px;overflow:auto;margin:8px 0}.exclusion-options label{display:flex;align-items:center;gap:10px;padding:10px 4px;margin:0;cursor:pointer}.exclusion-options input{width:auto}.exclusion-options span{min-width:0;overflow-wrap:anywhere}.exclusion-options b{font-size:12px;font-weight:550}.exclusion-options small{display:block;color:var(--muted);margin-top:4px}.exclusions-popover footer{border-top:1px solid var(--line);padding-top:12px;font-size:11px;color:var(--muted)}.exclusions-popover footer button{border:0;background:none;padding:4px}.excluded-tags{display:flex;flex-wrap:wrap;gap:5px;max-height:90px;overflow:auto;margin:10px 0}.excluded-tags button{display:flex;align-items:center;gap:5px;border:0;background:#edf4f0;border-radius:5px;padding:5px 7px;font-size:11px;max-width:100%;overflow-wrap:anywhere}
</style>
