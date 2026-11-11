import {onMounted,onUnmounted,ref,watch,type Ref} from 'vue'
import {api,type Row} from './lib'
import {subscriptionConfirmed} from './subscriptionStatus'
import {useOrderUpdates} from './orderUpdates'

const snapshots=new Map<string,Row>()
export function clearDashboardCache(){snapshots.clear()}

/** Keep a user's last snapshot on navigation; never retain it across sign-out. */
export function useDashboard(owner:string,path:Ref<string>,canReadOrders:()=>boolean=()=>true){
 const cacheKey=()=>owner+':'+path.value
 const data=ref<Row|undefined>(snapshots.get(cacheKey())),loading=ref(false),error=ref('')
 let controller:AbortController|undefined,timer:ReturnType<typeof setTimeout>|undefined,eventTimer:ReturnType<typeof setTimeout>|undefined,disposed=false,lastRead=0,pending=false
 function schedule(){clearTimeout(timer);if(!disposed&&!document.hidden&&data.value?.active)timer=setTimeout(()=>void refresh(),10000)}
 async function refresh(){
  if(disposed||document.hidden||loading.value)return
  const key=cacheKey(),request=new AbortController();controller=request;loading.value=true;error.value=''
  try{
   const result=await api(path.value,'GET',undefined,{},AbortSignal.any([request.signal,AbortSignal.timeout(15000)]))
   if(disposed||controller!==request)return
   data.value=result;lastRead=Date.now();snapshots.delete(key);snapshots.set(key,result)
   if(snapshots.size>12)snapshots.delete(snapshots.keys().next().value!)
  }catch(e){if(!disposed&&controller===request&&!request.signal.aborted)error.value=(e as Error).message}
  finally{if(controller===request){loading.value=false;schedule();if(pending){pending=false;queueChange()}}}
 }
 function visible(){if(document.hidden){clearTimeout(timer);controller?.abort();loading.value=false}else void refresh()}
 watch(path,()=>{controller?.abort();controller=undefined;loading.value=false;data.value=snapshots.get(cacheKey())||data.value;void refresh()})
 // Ready is not a data change. Preserve events received during a read and
 // coalesce bursts without losing a newly queued order after an idle snapshot.
 function queueChange(){
  if(disposed||document.hidden)return
  if(loading.value){pending=true;return}
  clearTimeout(eventTimer);eventTimer=setTimeout(()=>void refresh(),Math.max(100,1500-(Date.now()-lastRead)))
 }
 useOrderUpdates(id=>{if(id)queueChange()},{admin:path.value.startsWith('/admin/'),enabled:canReadOrders})
 onMounted(()=>{void refresh();document.addEventListener('visibilitychange',visible)})
 onUnmounted(()=>{disposed=true;controller?.abort();clearTimeout(timer);clearTimeout(eventTimer);document.removeEventListener('visibilitychange',visible)})
 return {data,loading,error,refresh}
}

export function dashboardStatus(row:Row){
 if(subscriptionConfirmed(row))return '订阅成功'
 if(row.business==='refund')return ({queued:'排队中',processing:'办理中',verifying:'核实中',attention:'待跟进',succeeded:'服务已完成',failed:'办理失败',banned:'账号不可用',cancelled:'已取消'} as Row)[row.status]||row.status
 if(row.business==='registration')return ({queued:'排队中',running:'正在交付',review:'状态待核实',completed:'全部交付',partial:'部分交付',failed:'未完成',cancelled:'已取消'} as Row)[row.status]||row.status
 if(row.status==='review')return '付款结果待核实'
 if(row.status==='succeeded'&&row.workflow==='subscription'){
  if(row.activation==='review')return '已付款 · 生效待核实'
  if(row.renewal==='review')return '已付款 · 续费待处理'
  if(row.activation==='pending'||['pending','retrying'].includes(row.renewal))return '已付款 · 正在收尾'
  return '订阅完成'
 }
 if(row.workflow==='link'&&['succeeded','awaiting_payment'].includes(row.status))return '结账链接已生成'
 return ({queued:'排队中',processing:'办理中',draft:'待提交',failed:'未完成',cancelled:'已取消',awaiting_payment:'等待付款',succeeded:'已完成'} as Row)[row.status]||row.status
}
