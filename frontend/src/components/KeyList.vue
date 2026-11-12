<script setup lang="ts">
import {ref,watch} from 'vue'
import {date,type Row} from '../lib'
import {useRemoteList} from '../useRemoteList'
import DataTable from './DataTable.vue'
import ListPager from './ListPager.vue'
const props=defineProps<{busy:boolean;revision:number}>()
const emit=defineEmits<{copy:[row:Row];rename:[row:Row];revoke:[row:Row]}>()
const q=ref(''),status=ref('')
const {page,size,sortBy,sortDir,data,rows:items,loading,error,load,sort}=useRemoteList('customer-keys',()=>'/keys',{q,status})
watch(()=>props.revision,load)
</script>
<template><div class="table-toolbar"><input v-model="q" type="search" placeholder="搜索用途名称或密钥标识" maxlength="200"/><select v-model="status"><option value="">全部状态</option><option value="active">有效</option><option value="revoked">已撤销</option></select></div><p v-if="error" class="alert danger">{{error}}</p><DataTable paged hide-toolbar hide-pagination :sort-by="sortBy" :sort-dir="sortDir" @sort="sort" :rows="items" :columns="[{key:'name',label:'用途名称'},{key:'prefix',label:'密钥标识'},{key:'status',sortKey:'revoked',label:'状态'},{key:'last_used',label:'最近使用'},{key:'action',label:'操作'}]"><template #prefix="{row}"><code>{{row.prefix}}</code></template><template #status="{row}"><span class="badge" :class="row.revoked?'disabled':'active'">{{row.revoked?'已撤销':'有效'}}</span></template><template #last_used="{row}">{{date(row.last_used)}}</template><template #action="{row}"><div class="key-actions"><button v-if="!row.revoked" class="text-btn" :disabled="busy||!row.copyable" :title="row.copyable?'复制完整密钥':'原密钥下次有效调用后可复制'" @click="emit('copy',row)">{{row.copyable?'复制':'待再次使用'}}</button><button class="text-btn" @click="emit('rename',row)">改名</button><button v-if="!row.revoked" class="text-btn danger-text" @click="emit('revoke',row)">撤销</button></div></template></DataTable><ListPager v-model:page="page" v-model:size="size" :total="data.total||0" :loading="loading"/></template>
