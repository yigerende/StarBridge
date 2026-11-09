export default function AdminRegistrationResources({proxies}:{proxies:boolean}){
 const title=proxies?'注册代理管理':'原注册管理台'
 return <div className="p-card"><h2>{title}</h2><p>注册代理池、会话代理、地区、执行任务和供应商配置完整保留。</p><a className="p-button primary" href={'/registration/admin/engine'+(proxies?'/proxies':'')}>打开{title}</a></div>
}
