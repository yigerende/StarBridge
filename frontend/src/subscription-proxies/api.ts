import type {ProxyCatalogSnapshot} from '../../../services/gpt-register/web/src/types'
import type {PageInfo} from '../../../services/gpt-register/web/src/components/Pagination'
import type {ProxyInventoryPage,ProxyTest} from './Inventory'
type Snapshot=ProxyCatalogSnapshot&PageInfo&{operation?:{id:string};settings?:{renewal_country:string}}
const base='/registration/api/admin/v1/subscription-proxies'
async function request<T>(path:string,method='GET',body?:unknown):Promise<T>{
 const r=await fetch(base+path,{method,credentials:'same-origin',headers:{'X-Requested-With':'gptreg',...(body!==undefined?{'Content-Type':'application/json'}:{})},...(body!==undefined?{body:JSON.stringify(body)}:{})})
 const value=await r.json()
 if(r.status===401)window.dispatchEvent(new Event('gptreg:session-expired'))
 if(!r.ok)throw Error(value.error||'代理请求未完成')
 return value
}
async function mutate<T>(path:string,method='POST',body:unknown={}):Promise<T>{
 const started=await request<{operation:string}>(path,method,body)
 for(;;){
  await new Promise(r=>setTimeout(r,700))
  const job=await request<{state:string;result:T;error?:string}>('/operations/'+started.operation)
  if(job.state==='failed')throw Error(job.error||'代理操作未完成')
  if(job.state==='completed')return job.result
 }
}
export const endpoints={
 proxiesPage:(_offset:number)=>request<Snapshot>(''),
 proxyEntries:(country:string,offset:number)=>request<PageInfo&{items:{id:string;url:string}[]}>(`/pools/${encodeURIComponent(country)}/entries?offset=${offset}&limit=50`),
 proxyInventory:(query:Record<string,string>)=>request<ProxyInventoryPage>('/inventory?'+new URLSearchParams(query)),
 testProxyInventory:(body:unknown)=>mutate<ProxyTest>('/inventory/test','POST',body),
 previewProxies:(body:unknown)=>request<any>('/preview','POST',body),
 importProxies:(body:unknown)=>mutate<ProxyCatalogSnapshot>('/import','POST',body),
 buildAndPullProxyApi:(body:unknown)=>mutate<ProxyCatalogSnapshot>('/api/build-pull','POST',body),
 saveProxyProviderSettings:(body:unknown)=>mutate<{ok:boolean;provider:string}>('/provider-settings','PUT',body),
 pullProxySource:(id:string)=>mutate<ProxyCatalogSnapshot>('/sources/'+encodeURIComponent(id)+'/pull'),
 pullAllProxySources:()=>mutate<ProxyCatalogSnapshot>('/sources/pull-all'),
 removeDynamicProxy:(body:{countryCode:string;sourceId?:string;entryId?:string})=>mutate<ProxyCatalogSnapshot>('/dynamic','DELETE',body),
 clearProxies:(_scope:'dynamic')=>mutate<ProxyCatalogSnapshot>('','DELETE'),
 saveSettings:(renewal_country:string)=>request('/settings','PUT',{renewal_country}),
}
