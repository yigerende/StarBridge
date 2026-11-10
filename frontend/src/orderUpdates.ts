import {ref,onMounted,onUnmounted,watch} from 'vue'
type Listener=(id?:string)=>void
function channel(url:string){
 const state=ref<'connecting'|'live'|'offline'>('offline'),listeners=new Set<Listener>()
 let source:EventSource|undefined,blocked=false
 function close(){source?.close();source=undefined;state.value='offline'}
 function broadcast(id?:string){for(const listener of listeners)listener(id)}
 function connect(){
  if(source||blocked||!listeners.size||document.hidden)return
  state.value='connecting';source=new EventSource(url)
  source.addEventListener('ready',()=>{state.value='live';broadcast()})
  source.addEventListener('order',e=>{state.value='live';broadcast(JSON.parse((e as MessageEvent).data).id)})
  source.addEventListener('revoked',()=>{blocked=true;close();window.dispatchEvent(new Event('session-revoked'))})
  source.addEventListener('forbidden',()=>{blocked=true;close()})
  source.onerror=()=>{state.value='offline'}
 }
 function visibility(){if(document.hidden)close();else connect()}
 return {state,add(listener:Listener){listeners.add(listener);if(listeners.size===1){blocked=false;document.addEventListener('visibilitychange',visibility)}connect()},remove(listener:Listener){listeners.delete(listener);if(!listeners.size){close();document.removeEventListener('visibilitychange',visibility)}}}
}
const customer=channel('/api/order-updates'),admin=channel('/api/admin/order-updates')
export const orderConnection=customer.state,adminOrderConnection=admin.state
export function useOrderUpdates(listener:Listener,options:{admin?:boolean;enabled?:()=>boolean}={}){
 const stream=options.admin?admin:customer
 let mounted=false
 function sync(){if(!mounted)return;if(options.enabled?.()!==false)stream.add(listener);else stream.remove(listener)}
 watch(()=>options.enabled?.(),sync)
 onMounted(()=>{mounted=true;sync()})
 onUnmounted(()=>{mounted=false;stream.remove(listener)})
}
// Coalesce bursts without a polling timer; changes received during a request
// trigger one additional fetch, so the final state cannot be lost.
export function serialRefresh(load:()=>Promise<void>){
 let active=false,pending=false
 return async()=>{pending=true;if(active)return;active=true;try{while(pending){pending=false;await load()}}finally{active=false}}
}
