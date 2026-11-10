export const customerFeatures=['home','register','accounts','subscribe','links','renewal','refund','orders','wallet','keys','invites','membership','docs','support','profile'] as const
export function featureAllowed(features:Record<string,boolean>|undefined,key:string){return (customerFeatures as readonly string[]).includes(key)&&features?.[key]!==false}
export function routeAllowed(features:Record<string,boolean>|undefined,path:string){return featureAllowed(features,path.slice(1))}
