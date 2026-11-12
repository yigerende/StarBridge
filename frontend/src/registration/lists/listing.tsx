import {useEffect,useState,type Dispatch,type SetStateAction,type ReactNode} from 'react'
import {listOwner,storedControls} from '../../listState'
type Row=Record<string,any>
export function useStored<T>(name:string,initial:T|(()=>T)):[T,Dispatch<SetStateAction<T>>]{
 const key=`starbridge:list:v1:${listOwner()}:${name}`
 const read=()=>{const fallback=typeof initial==='function'?(initial as ()=>T)():initial;try{return storedControls(JSON.parse(localStorage.getItem(key)||'null'),fallback)}catch{return fallback}}
 const [entry,setEntry]=useState(()=>({key,value:read()}))
 const current=entry.key===key?entry:{key,value:read()}
 if(entry.key!==key)setEntry(current)
 useEffect(()=>{try{localStorage.setItem(key,JSON.stringify(current.value))}catch{}},[key,current.value])
 const set:Dispatch<SetStateAction<T>>=next=>setEntry(old=>({key,value:typeof next==='function'?(next as (v:T)=>T)(old.key===key?old.value:current.value):next}))
 return [current.value,set]
}
export function useList(name:string,defaultSort='created_at'){
 const [saved,setSaved]=useStored(name,{offset:0,size:10,sortBy:defaultSort,sortDir:'desc'})
 const size=[10,50,100,500].includes(saved.size)?saved.size:10,offset=Number.isInteger(saved.offset)&&saved.offset>=0&&saved.offset<=1000000?saved.offset:0
 const sortBy=typeof saved.sortBy==='string'?saved.sortBy:defaultSort,sortDir=saved.sortDir==='asc'?'asc':'desc'
 const setOffset=(v:number)=>setSaved(s=>({...s,offset:v}))
 const setSize=(v:number)=>setSaved(s=>({...s,size:v,offset:0}))
 const sort=(field:string)=>setSaved(s=>({...s,offset:0,sortBy:field,sortDir:s.sortBy===field&&s.sortDir==='asc'?'desc':'asc'}))
 return {offset,size,sortBy,sortDir,setOffset,setSize,sort,params:{limit:String(size),offset:String(offset),sort_by:sortBy,sort_dir:sortDir}}
}
export type ListState=ReturnType<typeof useList>
export function SortHead({label,field,list}:{label:ReactNode;field?:string;list?:ListState}){return <th aria-sort={list&&field&&list.sortBy===field?(list.sortDir==='asc'?'ascending':'descending'):'none'}>{list&&field?<button type="button" className="list-sort-heading" onClick={()=>list.sort(field)}>{label}<span aria-hidden="true">{list.sortBy===field?(list.sortDir==='asc'?'↑':'↓'):'↕'}</span></button>:label}</th>}
export function Table({columns,rows,empty='暂无记录',listing}:{columns:{title:ReactNode;field?:string;render:(r:Row)=>ReactNode}[];rows:Row[];empty?:string;listing?:ListState}){return <div className="p-table-wrap"><table><thead><tr>{columns.map((c,i)=><SortHead key={i} label={c.title} field={c.field} list={listing}/>)}</tr></thead><tbody>{rows.map((r,i)=><tr key={r.id||i}>{columns.map((c,j)=><td key={j}>{c.render(r)}</td>)}</tr>)}{!rows.length&&<tr><td colSpan={columns.length}>{empty}</td></tr>}</tbody></table></div>}
export function Pagination({offset,total,count,size=10,onChange,onSize,busy=false}:{offset:number;total?:number;count?:number;size?:number;onChange:(n:number)=>void;onSize?:(n:number)=>void;busy?:boolean}){
 useEffect(()=>{if(busy||total===undefined)return;if(total>0&&offset>=total)onChange(Math.floor((total-1)/size)*size);else if(total===0&&offset)onChange(0)},[total,size,offset,count,busy])
 total=total??0
 return <div className="records-pagination p-pagination"><label>每页 <select aria-label="每页条数" value={size} onChange={e=>onSize?.(Number(e.target.value))} disabled={busy}>{[10,50,100,500].map(n=><option key={n} value={n}>{n}</option>)}</select> 条 · 共 {total} 条</label><div><button type="button" className="p-button secondary" disabled={busy||offset===0} onClick={()=>onChange(Math.max(0,offset-size))}>上一页</button><span>{Math.floor(offset/size)+1} / {Math.max(1,Math.ceil(total/size))}</span><button type="button" className="p-button secondary" disabled={busy||offset+size>=total} onClick={()=>onChange(offset+size)}>下一页</button></div></div>
}
