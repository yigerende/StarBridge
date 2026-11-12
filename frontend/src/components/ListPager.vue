<script setup lang="ts">
import {computed} from 'vue'
import {ChevronLeft,ChevronRight} from 'lucide-vue-next'
import {PAGE_SIZES} from '../listState'
const props=defineProps<{page:number;size:number;total:number;loading?:boolean}>()
const emit=defineEmits<{'update:page':[value:number];'update:size':[value:number];change:[]}>()
const pages=computed(()=>Math.max(1,Math.ceil(props.total/props.size)))
function move(value:number){emit('update:page',value);emit('change')}
function resize(event:Event){emit('update:size',Number((event.target as HTMLSelectElement).value));emit('update:page',1);emit('change')}
</script>
<template><div class="records-pagination list-pagination"><label>每页<select :value="size" aria-label="每页条数" :disabled="loading" @change="resize"><option v-for="n in PAGE_SIZES" :key="n" :value="n">{{n}}</option></select>条<span>共 {{total.toLocaleString()}} 条</span></label><div><button class="icon-btn bordered" :disabled="loading||page<=1" aria-label="上一页" @click="move(page-1)"><ChevronLeft :size="16"/></button><span>{{page}} / {{pages}}</span><button class="icon-btn bordered" :disabled="loading||page>=pages" aria-label="下一页" @click="move(page+1)"><ChevronRight :size="16"/></button></div></div></template>
<style scoped>.list-pagination{flex-wrap:wrap;gap:14px}.list-pagination label{display:flex;align-items:center;gap:10px}.list-pagination label>span{margin-left:8px}.list-pagination select{width:78px}@media(max-width:600px){.list-pagination>div{margin-left:auto}}</style>
