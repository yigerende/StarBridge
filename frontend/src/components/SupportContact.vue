<script setup lang="ts">
import {computed} from 'vue'
import {Headphones,Users,ArrowUpRight,Copy,MessageCircle} from 'lucide-vue-next'
import {copy,action,type Row} from '../lib'
const props=defineProps<{site:Row}>()
function contact(value:unknown){
 try{
  const url=new URL(String(value||''))
  if(!['http:','https:'].includes(url.protocol)||url.username||url.password)return {href:'',label:'暂未设置',account:''}
  const telegram=['t.me','telegram.me'].includes(url.hostname)
  const username=telegram&&/^\/[A-Za-z0-9_]{5,32}\/?$/.test(url.pathname)?'@'+url.pathname.replaceAll('/',''):''
  return {href:url.href,label:username||url.href,account:username||url.href,telegram}
 }catch{return {href:'',label:'暂未设置',account:''}}
}
const contacts=computed(()=>{
 const support=contact(props.site.support_url),group=contact(props.site.telegram_group_url)
 return [{key:'support',...support,title:support.telegram?'TG 客服':'客服支持',subtitle:'一对一咨询',description:'遇到订单、充值或账户问题，可以直接联系客服。',field:'客服账号',button:support.telegram?'联系 TG 客服':'联系客服',copy:'复制账号',copyValue:support.account,icon:Headphones},
  {key:'group',...group,title:'TG 群组',subtitle:'一起交流',description:'加入用户群，与其他用户交流使用经验，了解服务动态。',field:'群组地址',button:'加入 TG 群组',copy:'复制群链接',copyValue:group.href,icon:Users}]
})
</script>
<template>
 <div class="page-heading support-heading"><div><span class="eyebrow">WE ARE HERE TO HELP</span><h1>联系客服</h1><p>订单咨询、使用帮助与用户交流，都在这里。</p></div><div class="actions"><slot name="actions"/></div></div>
 <div class="support-grid">
  <article v-for="item in contacts" :key="item.key" class="panel support-card">
   <header><span class="support-icon"><component :is="item.icon" :size="23"/></span><div><h2>{{item.title}}</h2><p>{{item.subtitle}}</p></div></header>
   <p class="support-description">{{item.description}}</p>
   <div class="support-address"><span>{{item.field}}</span><b :class="{unconfigured:!item.href}">{{item.label}}</b></div>
   <div class="support-actions"><a v-if="item.href" :href="item.href" target="_blank" rel="noopener noreferrer" class="btn primary">{{item.button}}<ArrowUpRight :size="16"/></a><button v-else class="btn secondary" disabled>暂未开放联系</button><button class="btn secondary" :disabled="!item.href" @click="action(()=>copy(item.copyValue))"><Copy :size="15"/>{{item.copy}}</button></div>
  </article>
 </div>
 <p class="support-note"><MessageCircle :size="16"/>咨询订单问题时，请附上订单号和问题描述，方便客服核查。</p>
</template>
<style scoped>
.support-heading{gap:20px}.support-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px}.support-card{padding:28px;min-width:0}.support-card header{display:flex;align-items:center;gap:15px}.support-icon{display:grid;place-items:center;flex-shrink:0;width:48px;height:48px;background:#edf7f3;color:var(--green);border-radius:12px}.support-card h2{font-size:18px}.support-card header p{margin-top:5px;font-size:12px;color:var(--muted)}.support-description{font-size:13px;color:#607887;margin:24px 0}.support-address{padding:22px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}.support-address span{display:block;color:var(--muted);font-size:12px}.support-address b{display:block;margin-top:12px;font-size:23px;font-weight:650;color:var(--ink);overflow-wrap:anywhere}.support-address b.unconfigured{font-size:20px;color:var(--muted);font-weight:500}.support-actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:24px}.support-note{display:flex;align-items:flex-start;gap:9px;margin-top:22px;font-size:12px;color:var(--muted)}.support-note svg{flex-shrink:0;margin-top:2px}@media(max-width:850px){.support-grid{grid-template-columns:1fr;gap:16px}}@media(max-width:560px){.support-heading{align-items:flex-start;flex-direction:column}.support-card{padding:22px}.support-card h2{font-size:17px}.support-description{font-size:12px;margin:20px 0}.support-actions .btn{font-size:12px;padding:11px 12px}.support-address b{font-size:21px}}
</style>
