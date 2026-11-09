import { lazy, Suspense, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { NativeRouter } from '../../../services/gpt-register/web/src/router'
import { call, type Row } from '../../../services/gpt-register/web/src/portal/client'
import { unifiedRoute } from '../../../services/gpt-register/web/src/integration'
import Workspace from '../../../services/gpt-register/web/src/portal/Workspace'
import '../../../services/gpt-register/web/src/portal/portal.css'
import '../../../services/gpt-register/web/src/portal/workspace.css'
import '../../../services/gpt-register/web/src/portal/center.css'
import './native.css'

const Risk=lazy(()=>import('../../../services/gpt-register/web/src/portal/AdminRisk'))
const Resources=lazy(()=>import('./AdminRegistrationResources'))
const Accounts=lazy(()=>import('../../../services/gpt-register/web/src/portal/CustomerAccounts'))
const Orders=lazy(()=>import('../../../services/gpt-register/web/src/portal/CustomerCenter').then(m=>({default:m.CustomerOrderCenter})))
const Admin=lazy(()=>import('../../../services/gpt-register/web/src/portal/AdminPlatform'))
const Suppliers=lazy(()=>import('../../../services/gpt-register/web/src/portal/AdminSuppliers'))
const Finance=lazy(()=>import('../../../services/gpt-register/web/src/portal/AdminFinance'))
type Props={route:string;user:Row;pub:Row;financeFilter?:Row;navigate:(path:string)=>void;expired:()=>void}
const cache=new QueryClient({defaultOptions:{queries:{staleTime:30000,retry:false,refetchOnWindowFocus:false}}})
let owner=''
export function clearRegistrationCache(){owner='';cache.clear()}
function Page(props:Props){
 const me=useQuery({queryKey:['me'],queryFn:()=>call('/api/user/v1/me'),staleTime:60000})
 const pub=useQuery({queryKey:['public'],queryFn:()=>call('/api/public'),staleTime:60000})
 useEffect(()=>{if((me.error as any)?.status===401)props.expired()},[me.error])
 if(me.error||pub.error)return <div className="p-alert">{(me.error||pub.error)?.message}<button className="p-button secondary" onClick={()=>{void me.refetch();void pub.refetch()}}>重试</button></div>
 if(!me.data||!pub.data)return <div className="p-loading" role="status">正在读取服务信息…</div>
 const user={...me.data,points:(Number(props.user.balance)-Number(props.user.frozen))/100,frozen:Number(props.user.frozen)/100}
 const path=props.route
 if(path.startsWith('/admin/')&&user.role!=='admin')return <div className="p-alert">此页面仅对管理员开放</div>
 const feature=path==='/register'?'register':path==='/accounts'?'accounts':path==='/orders'?'orders':null
 if(feature&&user.role!=='admin'&&pub.data.features?.[feature]===false)return <div className="p-card"><h2>此功能暂未开放</h2></div>
 return <NativeRouter path={path} navigate={to=>{if(to.startsWith('/admin/engine'))location.assign('/registration'+to);else props.navigate(unifiedRoute(to)||to)}}>
  <Suspense fallback={<div className="p-loading" role="status">正在加载…</div>}>
   {path==='/register'?<Workspace page="register" user={user} pub={pub.data}/>:
    path==='/accounts'?<Accounts userId={user.id}/>:
    path==='/orders'?<Orders user={user} pub={pub.data}/>:
    path==='/admin/suppliers'?<Suppliers/>:
    path==='/admin/finance'?<Finance sharedFilter={props.financeFilter}/>:
    path==='/admin/risk-controls'?<Risk controlsOnly/>:
    path==='/admin/resources'||path==='/admin/engine'?<Resources proxies={path==='/admin/resources'}/>:
    <Admin key={path} tab={path.split('/').pop()}/>
   }
  </Suspense>
 </NativeRouter>
}
export function mountRegistration(element:HTMLElement,props:Props){
 if(owner!==props.user.id){cache.clear();owner=props.user.id}
 const root=createRoot(element)
 const render=(next:Props)=>root.render(<QueryClientProvider client={cache}><Page {...next}/></QueryClientProvider>)
 render(props)
 return {update:render,unmount:()=>root.unmount()}
}
