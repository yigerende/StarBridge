export const financeToday=()=>new Date(Date.now()+8*3600000).toISOString().slice(0,10)
export function moveFinancePeriod(anchor:string,period:string,direction:number){
 const d=new Date(anchor+'T00:00:00Z')
 if(!Number.isFinite(d.getTime()))return financeToday()
 if(period==='month'){d.setUTCDate(1);d.setUTCMonth(d.getUTCMonth()+direction)}
 else if(period==='year'){d.setUTCMonth(0,1);d.setUTCFullYear(d.getUTCFullYear()+direction)}
 else d.setUTCDate(d.getUTCDate()+direction*(period==='week'?7:1))
 return d.toISOString().slice(0,10)
}
export async function exportFinance(params:URLSearchParams,view:'accounts'|'ledger'){
 const p=new URLSearchParams(params);p.set('view',view)
 const response=await fetch('/api/admin/finance/reports/export?'+p,{credentials:'same-origin'})
 if(!response.ok){const error=await response.json().catch(()=>({}));throw Error(error.error||'导出失败，请稍后重试')}
 const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a')
 link.href=url;link.download=response.headers.get('content-disposition')?.match(/filename="([^"]+)"/)?.[1]||'starbridge-finance.csv'
 link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
}
