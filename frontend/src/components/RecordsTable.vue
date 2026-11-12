<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {ChevronLeft,ChevronRight,SearchX} from 'lucide-vue-next'
import type {Row} from '../lib'
import SortHeader from './SortHeader.vue'
import ListPager from './ListPager.vue'
const props=defineProps<{rows:Row[];columns:{key:string;label:string;wide?:boolean;sortKey?:string|false}[];remote?:boolean;page?:number;size?:number;total?:number;sortBy?:string;sortDir?:string;loading?:boolean;scope:string;filtered?:boolean}>()
defineEmits<{reset:[];sort:[field:string];'update:page':[value:number];'update:size':[value:number]}>()
const localPage=ref(1),localSize=ref(10)
const page=computed(()=>props.remote?props.page||1:localPage.value),size=computed(()=>props.remote?props.size||10:localSize.value)
const pages=computed(()=>Math.max(1,Math.ceil(props.rows.length/size.value)))
const visible=computed(()=>props.remote?props.rows:props.rows.slice((page.value-1)*size.value,page.value*size.value))
// Status refreshes preserve the current page; changing the result set resets it.
watch(()=>props.rows.map(r=>r.id).join(','),()=>{if(!props.remote)localPage.value=1})
watch(size,()=>{if(!props.remote)localPage.value=1})
</script>
<template>
 <div class="records-count" role="status"><span><b>{{remote?total:rows.length}}</b> 条{{filtered?'匹配':''}}记录 <span class="records-scope">· {{scope}}</span></span><span v-if="loading">正在刷新…</span></div>
 <div class="records-scroll" tabindex="0" aria-label="记录列表，可横向滚动">
  <table class="records-table"><thead><tr><SortHeader v-for="c in columns" :key="c.key" :field="c.sortKey||c.key" :label="c.label" :sort-by="sortBy" :sort-dir="sortDir" :disabled="!remote||c.sortKey===false||c.key==='action'" @sort="$emit('sort',$event)"/></tr></thead>
   <tbody><tr v-for="r in visible" :key="r.id"><td v-for="c in columns" :key="c.key" :data-label="c.label" :class="{'record-wide':c.wide}"><div class="record-cell"><slot :name="c.key" :row="r">{{r[c.key]??'—'}}</slot></div></td></tr></tbody>
  </table>
  <div v-if="!rows.length" class="records-empty"><SearchX :size="28"/><b>{{loading?'正在读取记录…':filtered?'没有符合条件的记录':'暂时没有记录'}}</b><p>{{filtered?'试试其他关键词，或清除筛选条件。':'新的业务记录会显示在这里。'}}</p><button v-if="filtered" class="text-btn" @click="$emit('reset')">清除筛选</button></div>
 </div>
 <ListPager :page="page" :size="size" :total="remote?total||0:rows.length" :loading="loading" @update:page="remote?$emit('update:page',$event):localPage=$event" @update:size="remote?$emit('update:size',$event):localSize=$event"/>

</template>
