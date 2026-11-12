<script setup lang="ts">
import {ref,watch} from 'vue'
import {cash,type Row} from '../lib'
import {useRemoteList} from '../useRemoteList'
import DataTable from './DataTable.vue'
import ListPager from './ListPager.vue'
const props=defineProps<{membership:Row}>()
const q=ref(''),business=ref(''),status=ref('')
const {data,rows,error,loading,page,size,sortBy,sortDir,load,sort}=useRemoteList('membership-quotes',()=>'/membership/services',{q,business,status},{sortBy:'business',sortDir:'asc'})
watch(()=>props.membership,()=>void load())
</script>
<template>
 <section class="panel membership-quotes">
  <form class="records-filters" @submit.prevent="load">
   <input v-model="q" type="search" maxlength="200" placeholder="搜索服务方案" aria-label="搜索服务报价"/>
   <select v-model="business" aria-label="报价业务"><option value="">全部业务</option><option value="registration">注册</option><option value="subscription">订阅</option><option value="refund">代退</option></select>
   <select v-model="status" aria-label="报价状态"><option value="">全部状态</option><option value="enabled">开放</option><option value="disabled">暂停</option></select>
   <button v-if="q||business||status" type="button" class="text-btn" @click="q='';business='';status=''">重置</button>
  </form>
  <p v-if="error" class="alert danger">{{error}}<button @click="load">重试</button></p>
  <DataTable paged :searchable="false" hide-toolbar hide-pagination :rows="rows" :sort-by="sortBy" :sort-dir="sortDir" @sort="sort" :columns="[{key:'business',label:'业务'},{key:'name',label:'服务方案'},{key:'fee',label:'标准服务费'},{key:'benefit',sortKey:'price_source',label:'计价方式'},{key:'member_fee',label:'我的服务费'},{key:'enabled',label:'状态'}]">
   <template #business="{row}">{{({registration:'注册',subscription:'订阅',refund:'代退'} as Row)[row.business]}}</template>
   <template #fee="{row}">{{cash(row.fee)}} 积分 / {{row.unit_label}}</template>
   <template #benefit="{row}">{{['product_override','account_override'].includes(row.price_source)?'专属价格':row.membership_eligible===false?'标准服务费':'会员减免 '+(100-(data.discount??membership.current.discount))+'%'}}<small v-if="row.membership_eligible===false">此服务不参与会员减免</small></template>
   <template #member_fee="{row}"><b class="positive">{{cash(row.member_fee)}} 积分 / {{row.unit_label}}</b><small v-if="row.saved>0">节省 {{cash(row.saved)}} 积分</small></template>
   <template #enabled="{row}"><span class="badge" :class="row.enabled?'active':'disabled'">{{row.enabled?'开放':'暂停'}}</span></template>
  </DataTable>
  <ListPager v-model:page="page" v-model:size="size" :total="data.total||0" :loading="loading"/>
 </section>
</template>
