import {createApp} from 'vue'
import App from './App.vue'
import './style.css'
import './records.css'
import './order-center.css'
window.addEventListener('vite:preloadError',event=>{
 const key='starbridge-asset-reload',last=Number(sessionStorage.getItem(key)||0)
 if(Date.now()-last>60000){
  event.preventDefault()
  sessionStorage.setItem(key,String(Date.now()))
  location.reload()
 }
})
createApp(App).mount('#app')
