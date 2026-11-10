// The gateway keeps the business monitor private and checks administrator access.
export async function riskApi(path:string,method='GET',body?:unknown,signal?:AbortSignal){
 const response=await fetch('/registration/api/admin/v1/risk'+path,{method,credentials:'same-origin',headers:{'X-Requested-With':'gptreg','Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal})
 const data=await response.json()
 if(!response.ok)throw new Error(data.error||data.message||'请求未完成')
 return data
}
