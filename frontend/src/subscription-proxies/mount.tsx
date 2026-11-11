import {createRoot} from 'react-dom/client'
import {QueryClient,QueryClientProvider} from '@tanstack/react-query'
import {Tooltip} from 'radix-ui'
import CountryProxies from './CountryProxies'
import './style.css'
export function mount(element:HTMLElement){
 const root=createRoot(element)
 const cache=new QueryClient({defaultOptions:{queries:{retry:false,refetchOnWindowFocus:false}}})
 document.body.classList.add('national-proxies-open')
 root.render(<QueryClientProvider client={cache}><Tooltip.Provider><CountryProxies/></Tooltip.Provider></QueryClientProvider>)
 return ()=>{root.unmount();cache.clear();document.body.classList.remove('national-proxies-open')}
}
