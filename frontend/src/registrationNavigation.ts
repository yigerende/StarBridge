export const registrationPaths:Record<string,string>={
 '/register':'/register','/accounts':'/accounts','/orders':'/orders',
 '/admin/orders':'/admin/orders','/admin/finance':'/admin/finance',
 '/admin/products':'/admin/products',
 '/admin/accounts':'/admin/accounts','/admin/suppliers':'/admin/suppliers','/admin/engine':'/admin/engine',
}
const fixed=new Set(['/register','/accounts','/admin/accounts','/admin/suppliers','/admin/engine'])
export function businessTabsFor(route:string){return route==='/wallet'||Object.hasOwn(registrationPaths,route)&&!fixed.has(route)}
export function registrationPageFor(route:string,business:string){return fixed.has(route)||business==='registration'&&route!='/wallet'&&businessTabsFor(route)}
