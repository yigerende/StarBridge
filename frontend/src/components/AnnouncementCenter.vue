<script setup lang="ts">
import {ref,computed,onMounted,onUnmounted,watch} from 'vue'
import {Bell,CheckCheck} from 'lucide-vue-next'
import {api,date,type Row} from '../lib'
import Modal from './Modal.vue'
import ListPager from './ListPager.vue'
import {rememberList,toggleSort} from '../listState'
const props=defineProps<{userId:string}>()
const items=ref<Row[]>([]),seen=ref<Record<string,string>>({}),opened=ref(false),unreadOnly=ref(false),loading=ref(false),error=ref('')
const page=ref(1),size=ref(10),q=ref(''),sortBy=ref('created_at'),sortDir=ref('desc'),total=ref(0),index=ref<Row[]>([]),filterSeen=ref<Record<string,string>>({})
rememberList('announcements',{page,size,q,sortBy,sortDir,unreadOnly})
let controller:AbortController|undefined,timer:ReturnType<typeof setTimeout>|undefined
const storageKey=()=>`xufu-announcements:${props.userId}`
const version=(item:Row)=>item.version
const unread=(item:Row)=>seen.value[item.id]!==version(item)
const count=computed(()=>index.value.filter(unread).length)
const expanded=ref(new Set<string>())
function toggle(item:Row,event:Event){const next=new Set(expanded.value);if((event.target as HTMLDetailsElement).open){next.add(item.id);expanded.value=next;mark(item)}else{next.delete(item.id);expanded.value=next}}
const visible=computed(()=>items.value)
function restore(){try{const value=JSON.parse(localStorage.getItem(storageKey())||'{}');seen.value=value&&typeof value==='object'&&!Array.isArray(value)?Object.fromEntries(Object.entries(value).filter(([k,v])=>k.length<=80&&typeof v==='string'&&/^[a-f0-9]{32}$/.test(v))) as Record<string,string>:{}}catch{seen.value={}}}
function save(){try{localStorage.setItem(storageKey(),JSON.stringify(seen.value))}catch{/* Reading remains available when storage is disabled. */}}
function mark(item:Row){seen.value={...seen.value,[item.id]:version(item)};save()}
function markAll(){seen.value=Object.fromEntries(index.value.map(item=>[item.id,version(item)]));save();filterSeen.value={...seen.value};void load()}
async function load(){controller?.abort();const request=new AbortController();controller=request;loading.value=true;error.value='';try{const result=await api('/announcements/query','POST',{page:page.value,page_size:size.value,q:q.value,sort_by:sortBy.value,sort_dir:sortDir.value,seen:filterSeen.value,unread:unreadOnly.value},{},request.signal);if(request===controller){index.value=result.index;total.value=result.total;const pages=Math.max(1,Math.ceil(result.total/size.value));if(page.value>pages){page.value=pages;return}items.value=result.items}}catch(e){if((e as Error).name!=='AbortError')error.value=(e as Error).message}finally{if(request===controller)loading.value=false}}
function sort(field:string){toggleSort(field,sortBy,sortDir,page)}
function show(){expanded.value=new Set();filterSeen.value={...seen.value};opened.value=true;void load()}
watch([page,size,sortBy,sortDir],()=>{clearTimeout(timer);timer=setTimeout(load,0)})
watch(size,()=>page.value=1)
watch([q,unreadOnly],()=>{controller?.abort();page.value=1;filterSeen.value={...seen.value};clearTimeout(timer);timer=setTimeout(load,250)})
function focus(){if(!document.hidden)void load()}
function storage(e:StorageEvent){if(e.key===storageKey())restore()}
watch(()=>props.userId,()=>{items.value=[];expanded.value=new Set();restore();void load()})
onMounted(()=>{restore();filterSeen.value={...seen.value};void load();window.addEventListener('focus',focus);window.addEventListener('storage',storage)})
onUnmounted(()=>{controller?.abort();clearTimeout(timer);window.removeEventListener('focus',focus);window.removeEventListener('storage',storage)})
</script>
<template><button class="announcement-trigger" @click="show" :aria-label="`公告，${count} 条未读`"><Bell :size="17"/><span>公告</span><b v-if="count">{{count>99?'99+':count}}</b></button>
<Modal v-if="opened" title="通知公告" @close="opened=false"><section class="announcement-center">
 <div class="announcement-toolbar"><div class="segmented"><button :class="{active:!unreadOnly}" @click="unreadOnly=false">全部公告</button><button :class="{active:unreadOnly}" @click="unreadOnly=true">未读（{{count}}）</button></div><button class="text-btn" :disabled="!count" @click="markAll"><CheckCheck :size="15"/>全部标为已读</button></div>
 <div class="announcement-toolbar"><input v-model="q" placeholder="搜索公告标题或内容" aria-label="搜索公告"/><button class="text-btn" @click="sort('title')">标题 {{sortBy==='title'?(sortDir==='asc'?'↑':'↓'):'↕'}}</button><button class="text-btn" @click="sort('created_at')">发布时间 {{sortBy==='created_at'?(sortDir==='asc'?'↑':'↓'):'↕'}}</button></div>
 <p v-if="error" class="alert danger" role="alert">{{error}} <button class="text-btn" @click="load">重试</button></p>
 <p v-if="!visible.length" class="empty">{{loading?'正在读取公告…':unreadOnly?'未读公告已全部处理':'暂无已发布公告'}}</p>
 <details v-for="item in visible" :key="item.id" class="announcement-item" open @toggle="toggle(item,$event)"><summary><b>{{item.title}}</b><small>{{unread(item)?'未读':'已读'}}</small></summary><div class="announcement-body">{{item.body}}</div><time>{{date(item.created_at)}}</time></details>
 <ListPager v-model:page="page" v-model:size="size" :total="total" :loading="loading"/>
 <p class="muted small">关闭后可从顶部“公告”重新查看。已读状态保存在当前浏览器。</p>
</section></Modal></template>
<style scoped>.announcement-trigger{display:flex;align-items:center;gap:6px;background:none;border:0;color:#3d7565;cursor:pointer;font-size:12px;white-space:nowrap}.announcement-trigger b{background:#d75252;color:white;border-radius:12px;font-size:10px;padding:2px 5px}.announcement-center{min-width:0}.announcement-toolbar{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:20px}.announcement-item{border-bottom:1px solid #e5ece9;padding:18px 0}.announcement-item summary{cursor:pointer;display:flex;gap:16px;justify-content:space-between;font-size:14px}.announcement-item summary small{color:#83988e;flex-shrink:0;font-size:11px}.announcement-body{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;line-height:1.9;margin:12px 0;color:#526e62}.announcement-item time{font-size:11px;color:#91a298}.announcement-center>.small{margin-top:20px}@media(max-width:600px){.announcement-toolbar{align-items:stretch;flex-direction:column}}</style>
