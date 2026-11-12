import { lazy, Suspense, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { NativeRouter } from '../../../services/gpt-register/web/src/router'
import { call, type Row } from '../../../services/gpt-register/web/src/portal/client'
import { unifiedRoute } from '../../../services/gpt-register/web/src/integration'
import { splitRoute } from '../registrationNavigation'
import Workspace, {OrderDetail} from '../../../services/gpt-register/web/src/portal/Workspace'
import '../../../services/gpt-register/web/src/portal/portal.css'
import '../../../services/gpt-register/web/src/portal/workspace.css'
import '../../../services/gpt-register/web/src/portal/center.css'
import './native.css'

const Risk=lazy(()=>import('../../../services/gpt-register/web/src/portal/AdminRisk'))
const Resources=lazy(()=>import('./AdminRegistrationResources'))
const Accounts=lazy(()=>import('./lists/CustomerAccounts'))
const Orders=lazy(()=>import('./lists/CustomerCenter').then(m=>({default:m.CustomerOrderCenter})))
const Admin=lazy(()=>import('./lists/AdminPlatform'))
const Suppliers=lazy(()=>import('./lists/AdminSuppliers'))
const Finance=lazy(()=>import('./lists/AdminFinance'))
type Props={route:string;search?:string;user:Row;pub:Row;financeFilter?:Row;detailId?:string;close?:()=>void;navigate:(path:string)=>void;expired:()=>void}
const cache=new QueryClient({defaultOptions:{queries:{staleTime:30000,retry:false,refetchOnWindowFocus:false}}})
let owner=''
export function clearRegistrationCache(){owner='';cache.clear()}
function unifiedTarget(to:string){const {path,search}=splitRoute(to);return (unifiedRoute(path)||path)+search}
function nativeHref(to:string){
 const target=unifiedTarget(to)
 if(target.startsWith('/admin/engine'))return '/registration'+target
 if(splitRoute(target).path==='/docs')return target
 return (target.startsWith('/admin')?'/admin':'/')+'?business=registration#'+target
}
function Page(props:Props){
 const me=useQuery({queryKey:['me'],queryFn:()=>call('/api/user/v1/me'),staleTime:60000})
 const pub=useQuery({queryKey:['public'],queryFn:()=>call('/api/public'),staleTime:60000})
 useEffect(()=>{if((me.error as any)?.status===401)props.expired()},[me.error])
 if(me.error||pub.error)return <div className="p-alert">{(me.error||pub.error)?.message}<button className="p-button secondary" onClick={()=>{void me.refetch();void pub.refetch()}}>重试</button></div>
 if(!me.data||!pub.data)return <div className="p-loading" role="status">正在读取服务信息…</div>
 const user={...me.data,features:props.user.features,points:(Number(props.user.balance)-Number(props.user.frozen))/100,frozen:Number(props.user.frozen)/100}
 const path=props.route
 if(path.startsWith('/admin/')&&user.role!=='admin')return <div className="p-alert">此页面仅对管理员开放</div>
 const feature=path==='/register'?'register':path==='/accounts'?'accounts':path==='/orders'?'orders':null
 if(feature&&props.user.features?.[feature]===false)return <div className="p-card"><h2>此功能暂未开放</h2></div>
 return <NativeRouter path={path} search={props.search} hrefFor={nativeHref} navigate={to=>{if(to.startsWith('/admin/engine'))location.assign('/registration'+to);else props.navigate(unifiedTarget(to))}}>
  <Suspense key={path+(props.search||'')} fallback={<div className="p-loading" role="status">正在加载…</div>}>
   {props.detailId&&path.startsWith('/admin/')?<OrderDetail id={props.detailId} admin onClose={()=>props.close?.()}/>:path==='/register'?<Workspace page="register" user={user} pub={pub.data}/>:
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
