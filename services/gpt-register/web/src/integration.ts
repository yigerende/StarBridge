declare const __STARBRIDGE_NATIVE__: boolean;
export const native = typeof __STARBRIDGE_NATIVE__ !== 'undefined' && __STARBRIDGE_NATIVE__;
const base = '/registration';
export const integrated = native || location.pathname === base || location.pathname.startsWith(base + '/');
export const embedded = native || integrated && window.parent !== window;
export const servicePath = (path: string) => integrated && path.startsWith('/') && !path.startsWith('//')
  && path !== base && !path.startsWith(base + '/') ? base + path : path;
const sharedRoutes:Record<string,string>={'/':'/home','/keys':'/keys','/wallet':'/wallet','/partners':'/invites','/profile':'/profile','/docs':'/docs','/admin':'/admin','/admin/users':'/admin/users','/admin/invites':'/admin/invites','/admin/risk':'/admin/risk','/admin/audit':'/admin/audit','/admin/codes':'/admin/codes','/admin/announcements':'/admin/announcements','/admin/settings':'/admin/settings'};
export function unifiedRoute(path:string){return sharedRoutes[path]}
export function openUnified(path:string){
  if(native){window.dispatchEvent(new CustomEvent('registration:navigate',{detail:path}));return;}
  if(embedded)window.parent.postMessage({type:'registration:navigate',path},location.origin);
  else location.assign(path==='/docs'?'/docs':'/?business=registration#'+path);
}

if (integrated && !native) {
  if(embedded){
    const report=()=>{const page=document.querySelector('.registration-embedded');if(page)window.parent.postMessage({type:'registration:resize',height:Math.ceil(page.getBoundingClientRect().height)+8},location.origin)};
    const root=document.getElementById('root');
    if(root){new ResizeObserver(report).observe(root);new MutationObserver(report).observe(root,{childList:true,subtree:true})}
  }
  const originalFetch = window.fetch.bind(window);
  document.documentElement.classList.add('starbridge-integrated');
  originalFetch('/api/public').then(r => r.ok ? r.json() : null).then(data => {
    const site = data?.site;
    if (!site) return;
    document.title = `${site.name || '星桥'} · ${site.english_name || 'StarBridge'}`;
    document.documentElement.style.setProperty('--unified-brand-name', JSON.stringify(site.english_name || 'StarBridge'));
  }).catch(() => {});
  window.fetch = (input, init) => originalFetch(typeof input === 'string' ? servicePath(input) : input, init);
  const OriginalEventSource = window.EventSource;
  window.EventSource = class extends OriginalEventSource {
    constructor(url: string | URL, options?: EventSourceInit) {
      super(typeof url === 'string' ? servicePath(url) : url, options);
    }
  };
  window.addEventListener('gptreg:session-expired', () => {
    if (embedded) window.parent.postMessage({ type: 'registration:session-expired' }, location.origin);
    else location.assign('/?business=registration');
  });
}
