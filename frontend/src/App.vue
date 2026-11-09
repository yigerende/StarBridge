<script setup lang="ts">
import {setSubscriptionRegions} from './lib'
import {onMounted,onUnmounted,ref,computed,defineAsyncComponent,watch} from 'vue'
import {Wallet,LayoutDashboard,ShieldCheck,LogOut,Menu,X,ReceiptText,KeyRound,UserRound,Gift,Sparkles,Bell,Settings2,BookOpen,Check,ChevronDown} from 'lucide-vue-next'
import {api,action,toast,cash,notify,type Row} from './lib'
import AnnouncementCenter from './components/AnnouncementCenter.vue'
import CustomMenuLinks from './components/CustomMenuLinks.vue'
import CustomPage from './components/CustomPage.vue'
import Customer from './Customer.vue'
import BusinessTabs from './components/BusinessTabs.vue'
import {businessTabsFor,registrationPageFor} from './registrationNavigation'
import RegistrationPanel from './components/RegistrationPanel.vue'
import ApiDocs from './components/ApiDocs.vue'
import AuthForm from './components/AuthForm.vue'
import {featureAllowed,routeAllowed} from './featureAccess'
const AdminChrome=defineAsyncComponent(()=>import('./components/AdminChrome.vue'))
const AdminFinanceCenter=defineAsyncComponent(()=>import('./components/AdminFinanceCenter.vue'))
const AdminRegistrationPricing=defineAsyncComponent(()=>import('./components/AdminRegistrationPricing.vue'))
const Admin=defineAsyncComponent(()=>import('./Admin.vue'))
const user=ref<Row|null>(null),pub=ref<Row>({site:{name:'星桥',english_name:'StarBridge'},products:[],announcements:[]}),loading=ref(true),mobile=ref(false)
const route=ref((location.pathname==='/docs'?'/docs':location.hash.slice(1)||'/home'))
window.addEventListener('hashchange',()=>{route.value=location.pathname==='/docs'?'/docs':location.hash.slice(1)||'/home';mobile.value=false})
const business=ref<'subscription'|'registration'>(new URLSearchParams(location.search).get('business')==='registration'||sessionStorage.getItem('xufu-business')==='registration'?'registration':'subscription')
const expandedMenus=ref<Record<string,boolean>>({'/subscribe':true,'/register':true})
watch(route,value=>{const parent=['/subscribe','/links','/renewal'].includes(value)?'/subscribe':['/register','/accounts'].includes(value)?'/register':null;if(parent)expandedMenus.value[parent]=true},{immediate:true})
watch(business,value=>sessionStorage.setItem('xufu-business',value))
watch(route,value=>{if(['/register','/accounts'].includes(value))business.value='registration';else if(value==='/subscribe')business.value='subscription'},{immediate:true})
const showBusinessTabs=computed(()=>!['/wallet','/admin/finance'].includes(route.value)&&businessTabsFor(route.value))
const registrationPage=computed(()=>route.value!=='/admin/finance'&&registrationPageFor(route.value,business.value))
const admin=computed(()=>user.value?.role==='admin'&&route.value.startsWith('/admin'))
function registrationNavigate(path:string){business.value='registration';if(path==='/docs')location.assign('/docs');else location.hash=path}
watch([route,user],()=>{if(user.value&&user.value.role!=='admin'&&route.value.startsWith('/admin'))location.hash='/home'})
function sessionRevoked(){void import('./registration/native').then(m=>m.clearRegistrationCache());user.value=null;location.hash='/home';notify('登录已失效，请重新登录')}
onMounted(()=>window.addEventListener('session-revoked',sessionRevoked));onUnmounted(()=>window.removeEventListener('session-revoked',sessionRevoked))
const navIcons:Row={home:LayoutDashboard,subscribe:Sparkles,orders:ReceiptText,wallet:Wallet,links:KeyRound,renewal:Settings2,keys:KeyRound,invites:Gift,membership:ShieldCheck,docs:BookOpen,profile:UserRound}
const customerNav=computed(()=>{
 const rows=((user.value?.navigation||[]) as Row[]).map(n=>[n.path,n.label,navIcons[n.id],['/links','/renewal'].includes(n.path)?'subscription':n.group])
 if(!rows.some(n=>n[0]==='/subscribe')&&rows.some(n=>n[3]==='subscription'))rows.splice(rows[0]?.[0]==='/home'?1:0,0,['/subscribe','订阅办理',Sparkles,'main'])
 const index=rows.findIndex(n=>n[0]==='/subscribe')
 rows.splice(index<0?1:index+1,0,['/register','注册办理',Sparkles,'main'],['/accounts','我的账号',KeyRound,'registration'])
 return rows.map(n=>[n[0],n[0]==='/wallet'?'资金中心':n[0]==='/docs'?'接口文档':n[1],n[2],n[3]])
})
const childMenus=computed<Record<string,typeof customerNav.value>>(()=>({'/subscribe':customerNav.value.filter(n=>n[3]==='subscription'),'/register':customerNav.value.filter(n=>n[3]==='registration')}))
const customMenus=computed(()=>user.value?.custom_menus||[])
const customPage=computed(()=>route.value.startsWith('/custom/')||admin.value&&route.value.startsWith('/admin/custom/'))
const customId=computed(()=>route.value.match(/^\/(?:admin\/)?custom\/([A-Za-z0-9_-]+)$/)?.[1]||'')
const enabled=(key:string)=>featureAllowed(user.value?.features||pub.value.features,key)
const visiblePrimary=computed(()=>customerNav.value.filter(n=>n[3]==='main'))
const visibleTools=computed(()=>customerNav.value.filter(n=>n[3]==='tools'))
const permitted=computed(()=>['/register','/accounts'].includes(route.value)||routeAllowed(user.value?.features||pub.value.features,route.value))
const title=computed(()=>customMenus.value.find((item:Row)=>customPage.value&&item.id===customId.value)?.label||customerNav.value.find(n=>n[0]===route.value)?.[1]||'我的空间');const goProfile=()=>location.hash='/profile'
async function refresh(){user.value=await api('/me');if(user.value&&route.value==='/home'&&!enabled('home'))location.hash='/profile'}
async function loadPublic(){pub.value=await api('/public');setSubscriptionRegions(pub.value.countries||[]);document.title=`${pub.value.site.name} · ${pub.value.site.english_name}`;const icon=document.querySelector<HTMLLinkElement>('link[rel=icon]');if(icon)icon.href=pub.value.site.icon_url||'/site-icon'}
async function authenticated(){await refresh();await loadPublic()}
async function logout(){await action(async()=>{await api('/auth/logout','POST',{});void import('./registration/native').then(m=>m.clearRegistrationCache());user.value=null;location.hash='/home'})}
let refreshTimer:ReturnType<typeof setInterval>;onUnmounted(()=>clearInterval(refreshTimer));
onMounted(async()=>{try{await loadPublic();try{await refresh()}catch{user.value=null}}catch(e){notify((e as Error).message)}finally{loading.value=false}refreshTimer=setInterval(()=>{if(user.value&&document.visibilityState==='visible')refresh().catch(()=>{})},30000)})
</script>
<template><div class="toast" v-if="toast" role="status"><Check :size="18"/>{{toast}}</div><div v-if="loading" class="boot"><img :src="pub.site.icon_url||'/site-icon'" width="36" height="36" alt="星桥"/><span>正在开启工作台…</span></div>
<div v-else-if="route==='/docs'&&enabled('docs')"><ApiDocs :site="pub.site"/></div><div v-else-if="!user" class="login-page"><div class="login-story"><a href="#/home" class="brand light"><span class="brand-icon"><img :src="pub.site.icon_url||'/site-icon'" alt=""/></span><b>{{pub.site.name}}<small>{{pub.site.english_name}}</small></b></a><div class="story-copy"><div class="pill light-pill"><span class="live-dot"/> SUBSCRIPTION, SIMPLIFIED</div><h1>每一份服务，<br/>都有清晰的<br/><em>下一步。</em></h1><p>从选择服务到查看结果，<br/>一个工作台，让每一笔安排井然有序。</p><div class="story-facts"><span><ShieldCheck :size="18"/>安全的账户空间</span><span><ReceiptText :size="18"/>可追溯的资金记录</span></div></div><div class="orbit orbit-one"></div><div class="orbit orbit-two"></div><div class="story-bottom">DESIGNED FOR CLARITY <span>01 / 简单 · 可靠 · 有序</span></div></div><div class="login-form-side"><div class="login-top">订阅与注册工作台 <span>中文 / ZH</span></div><AuthForm :pub="pub" @authenticated="authenticated"/><div v-if="pub.announcements.length" class="login-announcements"><Bell :size="16"/><div class="marquee"><div><span v-for="a in pub.announcements" :key="a.id">{{a.title}} · {{a.body}}</span></div></div></div><footer>© {{new Date().getFullYear()}} {{pub.site.english_name}} · 让服务更清晰</footer></div></div>
<div v-else :class="['shell',admin?'admin-shell':'customer-shell']"><header class="topbar"><a href="#/home" class="brand"><span class="brand-icon"><img :src="pub.site.icon_url||'/site-icon'" alt=""/></span><b>{{pub.site.name}}<small>{{pub.site.english_name}}</small></b></a><AdminChrome v-if="user.role==='admin'" part="label" :route="route" :admin="admin"/><div class="top-actions"><AnnouncementCenter :user-id="user.id"/><a v-if="user.membership&&enabled('membership')" href="#/membership" class="membership-badge" :title="user.membership.current.name">{{user.membership.current.code}} · {{user.membership.current.name}}</a><AdminChrome v-if="user.role==='admin'" part="entry" :route="route" :admin="admin"/><a v-if="enabled('wallet')" class="wallet-chip" href="#/wallet"><Wallet :size="15"/><b>{{cash(Number(user.balance||0)-Number(user.frozen||0))}}</b><small>积分</small></a><button class="avatar" @click="goProfile" :aria-label="user.name">{{user.name.slice(0,1)}}</button><button class="icon-btn mobile-only" @click="mobile=!mobile" aria-label="展开菜单"><Menu :size="21"/></button><button class="icon-btn desktop-only" @click="logout" title="退出登录" aria-label="退出登录"><LogOut :size="18"/></button></div></header>
<aside class="sidebar" :class="{mobileOpen:mobile}"><button class="icon-btn mobile-only close-nav" @click="mobile=false" aria-label="收起菜单"><X/></button><AdminChrome v-if="admin" part="navigation" :route="route" :admin="admin" :menus="customMenus"/><template v-else><div class="side-greeting"><span class="eyebrow">MY WORKSPACE</span><h3>让每一笔，井然有序。</h3></div><section v-if="visiblePrimary.length"><p>服务与资金</p><div v-for="n in visiblePrimary" :key="n[0]" class="customer-nav-group">
 <div class="customer-nav-row" :class="{'has-active-child':childMenus[n[0]]?.some(child=>child[0]===route)}">
  <button v-if="n[0]==='/subscribe'&&!enabled('subscribe')" class="nav-group-heading" :aria-expanded="expandedMenus[n[0]]" :aria-controls="'submenu-'+n[0].slice(1)" @click="expandedMenus[n[0]]=!expandedMenus[n[0]]"><component :is="n[2]" :size="18"/>{{n[1]}}</button>
  <a v-else :href="'#'+n[0]" :class="{active:route===n[0]}" :aria-current="route===n[0]?'page':undefined" @click="expandedMenus[n[0]]=true"><component :is="n[2]" :size="18"/>{{n[1]}}<span v-if="route===n[0]" class="nav-dot"></span></a>
  <button v-if="childMenus[n[0]]?.length" class="icon-btn submenu-toggle" :aria-label="(expandedMenus[n[0]]?'收起':'展开')+n[1]+'子菜单'" :aria-expanded="expandedMenus[n[0]]" :aria-controls="'submenu-'+n[0].slice(1)" @click="expandedMenus[n[0]]=!expandedMenus[n[0]]"><ChevronDown :size="15" :class="{collapsed:!expandedMenus[n[0]]}"/></button>
 </div>
 <ul v-if="childMenus[n[0]]?.length" v-show="expandedMenus[n[0]]" :id="'submenu-'+n[0].slice(1)" class="customer-submenu" :aria-label="n[1]+'子菜单'">
  <li v-for="child in childMenus[n[0]]" :key="child[0]"><a :href="'#'+child[0]" :class="{active:route===child[0]}" :aria-current="route===child[0]?'page':undefined"><component :is="child[2]" :size="15"/>{{child[1]}}<span v-if="route===child[0]" class="nav-dot"></span></a></li>
 </ul>
</div></section><section><p>工具与账户</p><a v-for="n in visibleTools" :key="n[0]" :href="n[0]==='/docs'?'/docs':'#'+n[0]" :target="n[0]==='/docs'?'_blank':undefined" :rel="n[0]==='/docs'?'noopener noreferrer':undefined" :class="{active:route===n[0]}"><component :is="n[2]" :size="18"/>{{n[1]}}<span v-if="route===n[0]" class="nav-dot"></span></a></section><CustomMenuLinks :items="customMenus" :route="route"/><div class="side-foot"><ShieldCheck :size="17"/><span>你的专属服务空间<small>CLARITY IN EVERY STEP</small></span></div><button class="btn secondary mobile-only" @click="logout" style="margin-top:20px;width:100%">退出登录</button></template></aside>
<main :class="['main',admin?'admin-main':'customer-main']"><div class="breadcrumb"><span>{{admin?'运营中心':'我的空间'}}</span><span>/</span><AdminChrome v-if="admin" part="title" :route="route" :admin="admin" :menus="customMenus"/><b v-else>{{title}}</b><span class="breadcrumb-end">{{new Date().toLocaleDateString('zh-CN',{month:'long',day:'numeric',weekday:'long'})}}</span></div><BusinessTabs v-if="showBusinessTabs" v-model="business"/><AdminRegistrationPricing v-if="admin&&route==='/admin/products'&&business==='registration'" :enabled="pub.registration_enabled===true"/><RegistrationPanel v-else-if="registrationPage&&!customPage" :route="route" :enabled="pub.registration_enabled===true" :user="user" :pub="pub" @refresh="refresh" @expired="sessionRevoked" @navigate="registrationNavigate"/><CustomPage v-else-if="customPage" :id="customId" :menus="customMenus"/><AdminFinanceCenter v-else-if="admin&&route==='/admin/finance'" :user="user" :pub="pub" @refresh="refresh" @expired="sessionRevoked" @navigate="registrationNavigate"/><Admin v-else-if="admin&&user.role==='admin'" :key="route" :route="route" :user="user" @refresh="refresh" @public-refresh="loadPublic();refresh()"/><div v-else-if="admin" class="empty"><ShieldCheck/><h2>此页面仅对管理员开放</h2><a href="#/home" class="btn primary">返回工作台</a></div><div v-else-if="!permitted" class="panel empty"><ShieldCheck/><h2>此功能暂未开放</h2><p>可以通过左侧菜单使用已开放的服务。</p><a href="#/profile" class="btn primary">账户设置</a></div><Customer v-else :key="route+JSON.stringify(user.features)" :route="route" :business="business" :user="user" :pub="pub" @business="business=$event" @refresh="refresh" @logout="logout"/><footer class="workspace-footer"><span>{{pub.site.english_name}} / 清晰记录每一步</span><div><a v-if="enabled('keys')" href="#/keys">开发接入</a><a href="#/profile">账户设置</a><a v-if="pub.site.support_url" :href="pub.site.support_url" target="_blank" rel="noopener noreferrer">联系支持</a></div></footer></main></div></template>

<style scoped>
.customer-nav-row{display:flex;align-items:center;gap:2px;margin-bottom:5px}
.sidebar .customer-nav-row>a,.nav-group-heading{flex:1;min-width:0;margin:0}
.nav-group-heading{display:flex;align-items:center;gap:12px;padding:11px 14px;font-size:12px;color:#8294a0;border-radius:8px;text-align:left}
.has-active-child>a,.has-active-child>.nav-group-heading{color:var(--green)}
.submenu-toggle{min-width:28px;min-height:36px}
.submenu-toggle svg{transition:transform .15s}
.submenu-toggle svg.collapsed{transform:rotate(-90deg)}
.customer-submenu{list-style:none;padding:0 0 0 12px;margin:2px 0 8px 22px;border-left:1px solid var(--line)}
.sidebar .customer-submenu a{gap:9px;padding:10px 9px;font-size:11px}
</style>
