import {onMounted,onUnmounted,readonly,ref} from 'vue'

export type OrderCounts={total:number;registration:number;subscription:number;refund:number;updated_at:string}
const data=ref<OrderCounts|null>(null),failed=ref(false)
let users=0,last=0,pending:Promise<void>|null=null,timer:ReturnType<typeof setInterval>|undefined
async function refresh(){
 if(document.hidden||pending||Date.now()-last<60000)return
 last=Date.now()
 pending=(async()=>{
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),8000)
  try{
   const response=await fetch('/api/public/order-counts',{signal:controller.signal,credentials:'same-origin'})
   if(!response.ok)throw new Error('Unavailable')
   const result=await response.json()
   if(!['total','registration','subscription','refund'].every(key=>Number.isSafeInteger(result[key])&&result[key]>=0))throw new Error('Invalid counts')
   data.value=result;failed.value=false
  }catch{failed.value=true}finally{clearTimeout(timeout);pending=null}
 })()
 return pending
}
function visible(){if(!document.hidden)void refresh()}
export function usePublicCounts(){
 onMounted(()=>{if(++users===1){timer=setInterval(()=>void refresh(),60000);document.addEventListener('visibilitychange',visible)}void refresh()})
 onUnmounted(()=>{if(--users===0){clearInterval(timer);document.removeEventListener('visibilitychange',visible)}})
 return {counts:readonly(data),unavailable:readonly(failed)}
}
export const countText=(value:number|null|undefined)=>value==null?'—':value.toLocaleString('zh-CN')
