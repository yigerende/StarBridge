export const registrationPaths:Record<string,string>={
 '/register':'/register','/accounts':'/accounts','/orders':'/orders',
 '/admin/orders':'/admin/orders','/admin/finance':'/admin/finance',
 '/admin/resources':'/admin/engine/proxies','/admin/products':'/admin/products',
 '/admin/settings':'/admin/settings','/admin/risk':'/admin/risk','/admin/audit':'/admin/audit',
 '/admin/codes':'/admin/codes','/admin/announcements':'/admin/announcements',
 '/admin/accounts':'/admin/accounts','/admin/suppliers':'/admin/suppliers','/admin/engine':'/admin/engine',
}
const fixed=new Set(['/register','/accounts','/admin/accounts','/admin/suppliers','/admin/engine'])
export function businessTabsFor(route:string){return route==='/wallet'||Object.hasOwn(registrationPaths,route)&&!fixed.has(route)}
export function registrationPageFor(route:string,business:string){return fixed.has(route)||business==='registration'&&route!='/wallet'&&businessTabsFor(route)}
