import {watch,isReadonly,type Ref} from 'vue'

export const PAGE_SIZES=[10,50,100,500] as const
let owner=''
export function setListOwner(id:string){owner=id}
export function listOwner(){return owner}

export function storedControls<T>(saved:unknown,fallback:T):T{
 if(saved==null||typeof saved!==typeof fallback)return fallback
 if(typeof fallback==='string')return (typeof saved==='string'&&saved.length<=500?saved:fallback) as T
 if(typeof fallback==='number')return (typeof saved==='number'&&Number.isFinite(saved)?saved:fallback) as T
 if(typeof fallback==='boolean')return saved as T
 if(fallback&&typeof fallback==='object'&&!Array.isArray(fallback)&&!Array.isArray(saved))return Object.fromEntries(Object.entries(fallback).map(([key,value])=>[key,storedControls((saved as Record<string,unknown>)[key],value)])) as T
 return fallback
}

/** Store controls only, never rows, credentials or an authentication token. */
export function rememberList(name:string,fields:Record<string,Ref>,allowedSorts?:string[]){
 const storageKey=`starbridge:list:v1:${owner}:${name}`
 try{
  const saved=JSON.parse(localStorage.getItem(storageKey)||'null')
  if(saved&&typeof saved==='object')for(const [key,target] of Object.entries(fields)){
   const value=saved[key]
   if(value===undefined||typeof value!==typeof target.value||isReadonly(target))continue
   if(key==='page'&&(!Number.isInteger(value)||value<1||value>1000000))continue
   if(key==='size'&&!PAGE_SIZES.includes(value))continue
   if(key==='sortBy'&&allowedSorts&&!allowedSorts.includes(value))continue
   if(key==='sortDir'&&!['asc','desc'].includes(value))continue
   if(typeof value==='string'&&value.length>500)continue
   target.value=storedControls(value,target.value)
  }
 }catch{/* Storage may be unavailable or cleared by the browser. */}
 watch(Object.values(fields),()=>{
  try{localStorage.setItem(storageKey,JSON.stringify(Object.fromEntries(Object.entries(fields).map(([key,value])=>[key,value.value]))))}catch{}
 },{deep:true,flush:'sync'})
}

export function toggleSort(field:string,sortBy:Ref<string>,sortDir:Ref<string>,page:Ref<number>){
 sortDir.value=sortBy.value===field&&sortDir.value==='asc'?'desc':'asc'
 sortBy.value=field;page.value=1
}
