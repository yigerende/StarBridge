<script setup lang="ts">
import {ref} from 'vue'
import {type Row,date} from '../lib'
import {useRemoteList} from '../useRemoteList'
import DataTable from './DataTable.vue'
import ListPager from './ListPager.vue'
const emit=defineEmits<{order:[row:Row]}>()
const q=ref(''),renewal=ref('')
const {page,size,sortBy,sortDir,data,rows,loading,error,sort}=useRemoteList('subscription-renewals',()=>'/orders',{q,renewal},{extra:()=>({workflow:'subscription'})})
const states:Record<string,string>={kept:'保留自动续费',disabled:'已关闭自动续费',pending:'等待处理',review:'待核实',not_started:'尚未开始'}
</script><template><section class="panel"><div class="table-toolbar"><input v-model="q" type="search" placeholder="搜索账号或订单号" maxlength="200"/><select v-model="renewal"><option value="">全部续费状态</option><option v-for="(label,value) in states" :key="value" :value="value">{{label}}</option></select></div><p v-if="error" class="alert danger">{{error}}</p><DataTable paged hide-toolbar hide-pagination :rows="rows" :sort-by="sortBy" :sort-dir="sortDir" @sort="sort" :columns="[{key:'email',label:'订阅账号'},{key:'product_name',label:'方案'},{key:'renewal',label:'续费设置'},{key:'created_at',label:'提交时间'},{key:'action',label:'详情'}]"><template #renewal="{row}">{{states[row.renewal]||row.renewal}}</template><template #created_at="{row}">{{date(row.created_at)}}</template><template #action="{row}"><button class="text-btn" @click="emit('order',row)">查看进度</button></template></DataTable><ListPager v-model:page="page" v-model:size="size" :total="data.total||0" :loading="loading"/></section></template>
