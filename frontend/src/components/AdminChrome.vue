<script setup lang="ts">
import SystemUpdate from './SystemUpdate.vue'
import {computed} from 'vue'
import CustomMenuLinks from './CustomMenuLinks.vue'
import type {Row} from '../lib'
import {Globe2,LayoutDashboard,Activity,ChartNoAxesCombined,Users,Wallet,Ticket,Gift,Package,ShieldCheck,Server,Bell,ScrollText,Settings2,ArrowUpRight} from 'lucide-vue-next'
const props=defineProps<{part:'navigation'|'entry'|'label'|'title';route:string;admin:boolean;menus?:Row[]}>()
const groups=[{title:'运营洞察',items:[['/admin','运营概览',LayoutDashboard],['/admin/orders','订单中心',Activity],['/admin/accounts','注册账号交付',Package],['/admin/finance','资金中心',ChartNoAxesCombined],['/admin/suppliers','注册供应商统计',ChartNoAxesCombined]]},{title:'客户与经营',items:[['/admin/users','账户管理',Users],['/admin/topups','充值审核',Wallet],['/admin/codes','兑换码',Ticket],['/admin/invites','邀请管理',Gift],['/admin/products','服务与定价',Package],['/admin/membership','会员管理',ShieldCheck]]},{title:'安全与配置',items:[['/admin/risk','风控监控',ShieldCheck],['/admin/resources','代理管理',Server],['/admin/regions','订阅地区',Globe2],['/admin/announcements','公告中心',Bell],['/admin/audit','操作审计',ScrollText],['/admin/settings','站点设置',Settings2]]}] as const
const title=computed(()=>{const custom=props.menus?.find(item=>'/admin/custom/'+item.id===props.route);if(custom)return custom.label;for(const group of groups){const item=group.items.find(n=>n[0]===props.route);if(item)return item[1]}return '运营概览'})
</script>
<template><SystemUpdate v-if="part==='label'" compact/>
 <template v-if="part==='navigation'"><div class="side-greeting"><span class="eyebrow">OPERATIONS</span><h3>掌握每一处变化。</h3></div><section v-for="g in groups" :key="g.title"><p>{{g.title}}</p><a v-for="n in g.items" :key="n[0]" :href="'#'+n[0]"   :class="{active:route===n[0]}"><component :is="n[2]" :size="18"/>{{n[1]}}<span v-if="route===n[0]" class="nav-dot"></span></a></section><CustomMenuLinks :items="menus||[]" :route="route" prefix="/admin/custom/"/><div class="side-foot"><span class="live-dot"/><span>独立服务空间<small>STARBRIDGE · 订阅与注册</small></span></div></template>
 <a v-else-if="part==='entry'" :href="admin?'#/home':'#/admin'" target="_blank" rel="noopener noreferrer" class="mode-switch"><span class="mode-full">{{admin?'客户工作台':'运营控制台'}}</span><span class="mode-short">{{admin?'工作台':'运营'}}</span><ArrowUpRight :size="14"/></a>
 <span v-else-if="part==='label'" class="console-label">{{admin?'运营控制台':'服务工作台'}} <span class="pill">{{admin?'ADMIN':'WORKSPACE'}}</span></span>
 <b v-else>{{title}}</b>
</template>
<style scoped>.mode-switch{white-space:nowrap;flex-shrink:0}.mode-short{display:none}@media(max-width:700px){.mode-full,.mode-switch svg{display:none}.mode-short{display:inline}}</style>
