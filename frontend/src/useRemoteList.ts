import {ref,watch,onUnmounted,type Ref} from 'vue'
import {api,type Row} from './lib'
import {rememberList,toggleSort} from './listState'

export function useRemoteList(name:string,endpoint:()=>string,filters:Record<string,Ref>,defaults:{sortBy?:string;sortDir?:string;size?:number;extra?:()=>Row}={}){
 const page=ref(1),size=ref(defaults.size||10),sortBy=ref(defaults.sortBy||'created_at'),sortDir=ref(defaults.sortDir||'desc')
 const data=ref<Row>({items:[],total:0}),rows=ref<Row[]>([]),loading=ref(false),error=ref('')
 rememberList(name,{...filters,page,size,sortBy,sortDir})
 let controller:AbortController|undefined,timer:ReturnType<typeof setTimeout>|undefined,disposed=false
 async function load(){
  clearTimeout(timer);if(!endpoint())return;controller?.abort();const request=new AbortController();controller=request;loading.value=true;error.value=''
  const params=new URLSearchParams({...defaults.extra?.(),...Object.fromEntries(Object.entries(filters).map(([k,v])=>[k,String(v.value??'')])),page:String(page.value),page_size:String(size.value),sort_by:sortBy.value,sort_dir:sortDir.value})
  try{
   const result=await api(endpoint()+'?'+params,'GET',undefined,{},request.signal)
   if(disposed||controller!==request)return
   const pages=Math.max(1,Math.ceil(result.total/size.value))
   if(page.value>pages){page.value=pages;return}
   data.value=result;rows.value=result.items||[]
  }catch(e){if(!disposed&&controller===request&&(e as Error).name!=='AbortError')error.value=(e as Error).message}
  finally{if(controller===request)loading.value=false}
 }
 function sort(field:string){toggleSort(field,sortBy,sortDir,page)}
 watch(Object.values(filters),()=>{controller?.abort();page.value=1;clearTimeout(timer);timer=setTimeout(load,250)},{deep:true})
 watch(size,()=>{page.value=1})
 watch([page,size,sortBy,sortDir],()=>{clearTimeout(timer);timer=setTimeout(load,0)})
 watch(endpoint,(value,previous)=>{controller?.abort();if(value&&previous)page.value=1;void load()})
 void load()
 onUnmounted(()=>{disposed=true;clearTimeout(timer);controller?.abort()})
 return {page,size,sortBy,sortDir,data,rows,loading,error,load,sort}
}
