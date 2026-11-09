import {ref} from 'vue'
import {api,type Row} from './lib'
export const systemUpdate=ref<Row>({}),updateError=ref(''),updateSubmitting=ref(false)
export const updateBusy=new Set(['checking','fetching','building','waiting','backing_up','switching','verifying','rolling_back','recovery_required'])
let users=0,timer:ReturnType<typeof setTimeout>|undefined,running=false
export async function refreshUpdate(){
 if(running)return
 running=true
 try{systemUpdate.value=await api('/admin/system/update');updateError.value=''
  const waiting=sessionStorage.getItem('starbridge-restart')
  if(waiting&&waiting===systemUpdate.value.operation_id&&systemUpdate.value.phase==='completed'){
   sessionStorage.removeItem('starbridge-restart')
   const destination=systemUpdate.value.public_origin
   if(destination&&new URL(destination).origin!==location.origin)location.assign(destination+'/')
   else location.reload()
  }
 }
 catch(e){updateError.value=(e as Error).message}
 finally{running=false}
}
async function tick(){await refreshUpdate();if(users)timer=setTimeout(tick,updateBusy.has(systemUpdate.value.phase)||systemUpdate.value.pending?1500:15000)}
export function watchUpdates(){if(++users===1)void tick();return ()=>{if(--users===0)clearTimeout(timer)}}
export async function updateAction(action:string,body:Row={}){
 updateSubmitting.value=true;updateError.value=''
 try{const result=await api('/admin/system/update/'+action,'POST',body);if(['restart','rollback'].includes(action))sessionStorage.setItem('starbridge-restart',result.operation_id);systemUpdate.value={...systemUpdate.value,pending:true};await refreshUpdate()}
 catch(e){updateError.value=(e as Error).message}
 finally{updateSubmitting.value=false}
}
