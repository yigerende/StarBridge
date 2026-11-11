<script setup lang="ts">
import {computed,ref} from 'vue'
import {ArrowUpRight,ArrowRight,Check,Copy,KeyRound,LogOut,Pencil,ShieldCheck,Mail,CalendarDays,UserRound} from 'lucide-vue-next'
import {api,action,cash,date,copy,notify,type Row} from '../lib'
import Modal from './Modal.vue'
import {featureAllowed} from '../featureAccess'

const props=defineProps<{user:Row}>()
const enabled=(name:string)=>featureAllowed(props.user.features,name)
const emit=defineEmits<{refresh:[];logout:[];order:[Row]}>()
const membership=computed(()=>enabled('membership')?props.user.membership:null)
const modal=ref(''),busy=ref(false),formError=ref(''),name=ref(''),oldPassword=ref(''),newPassword=ref(''),confirmPassword=ref('')
const facts=computed(()=>[
 {name:'账户状态',value:props.user.status==='disabled'?'已停用':'正常',icon:ShieldCheck,note:'注册、订阅与代退共用此账户'},
 {name:'账户类型',value:props.user.role==='admin'?'管理员':'客户',icon:UserRound,note:'操作权限以账户角色为准'},
 {name:'加入时间',value:props.user.created_at?new Date(props.user.created_at).toLocaleDateString('zh-CN'):'—',icon:CalendarDays,note:'账户创建日期'},
 {name:'登录方式',value:'邮箱与密码',icon:Mail,note:'使用上方账户邮箱登录'},
])
function open(kind:string){name.value=props.user.name;oldPassword.value='';newPassword.value='';confirmPassword.value='';formError.value='';modal.value=kind}
function close(){if(busy.value)return;modal.value='';oldPassword.value='';newPassword.value='';confirmPassword.value='';formError.value=''}
async function save(){
 if(busy.value)return
 formError.value=''
 if(modal.value==='password'&&newPassword.value!==confirmPassword.value){formError.value='两次输入的新密码不一致。';return}
 busy.value=true
 try{
  const result=await api('/me','PATCH',modal.value==='password'?{name:props.user.name,old_password:oldPassword.value,password:newPassword.value}:{name:name.value.trim()})
  oldPassword.value='';newPassword.value='';confirmPassword.value='';modal.value=''
  if(result.relogin){notify('密码已更新，请重新登录');emit('logout')}else{notify('称呼已更新');emit('refresh')}
 }catch(e){formError.value=(e as Error).message}finally{busy.value=false}
}
</script>

<template>
 <div class="profile-page">
  <div class="page-heading profile-heading">
   <div><span class="eyebrow">YOUR ACCOUNT, AT A GLANCE</span><h1>个人设置</h1><p>管理账户资料、会员权益与登录安全。</p></div>

  </div>

  <div class="profile-overview">
   <section class="profile-identity">
    <div class="profile-person"><span class="profile-avatar">{{user.name.slice(0,1)}}</span><div><h2>{{user.name}}</h2><div class="profile-email"><span>{{user.email}}</span><button @click="action(()=>copy(user.email))" aria-label="复制账户邮箱"><Copy :size="13"/></button></div></div><button class="profile-edit" @click="open('name')" aria-label="编辑称呼" title="编辑称呼"><Pencil :size="16"/></button></div>
    <div v-if="enabled('wallet')" class="profile-balance"><span>账户可用金额</span><strong>{{cash(Number(user.balance||0)-Number(user.frozen||0))}}<small>积分</small></strong><p>冻结金额 <b>{{cash(user.frozen)}} 积分</b></p></div>
    <div class="profile-identity-foot"><span><ShieldCheck :size="15"/>{{user.role==='admin'?'管理员账户':'客户账户'}}</span><a v-if="enabled('wallet')" href="#/wallet">管理资金<ArrowUpRight :size="16"/></a></div>
   </section>
   <div class="profile-metrics"><article v-for="fact in facts" :key="fact.name" class="panel profile-metric profile-fact"><div><span>{{fact.name}}</span><component :is="fact.icon" :size="18"/></div><strong>{{fact.value}}</strong><p>{{fact.note}}</p></article></div>
  </div>
  <section v-if="membership" class="panel profile-membership">
   <div class="profile-tier"><span class="profile-tier-emblem"><ShieldCheck :size="23"/><b>{{membership.current.code}}</b></span><div><span class="eyebrow">MEMBERSHIP</span><h2>{{membership.current.name}}</h2><p>{{membership.fee_override!=null?'专属服务单价 '+cash(membership.fee_override)+' 积分 / 次':'服务费减免 '+(100-membership.current.discount)+'%'}}</p></div></div>
   <div class="profile-growth"><div><span>会员成长</span><b>{{cash(membership.spent)}} <small>积分 累计成长</small></b></div><div class="profile-growth-track" role="progressbar" aria-label="账户会员成长" :aria-valuenow="membership.progress" aria-valuemin="0" aria-valuemax="100"><i :style="{width:membership.progress+'%'}"></i></div><p v-if="membership.next">距离自动升级至 <b>{{membership.next.name}}</b>，还需 {{cash(membership.remaining)}} 积分</p><p v-else>已达到最高等级</p><small v-if="membership.growth_credit>0">已包含等级调整{{membership.until?' · 本次调整有效至 '+date(membership.until):''}}</small></div>
   <a class="profile-member-link" href="#/membership">查看权益<ArrowRight :size="16"/></a>
  </section>
  <section class="panel profile-security">
   <div class="profile-security-heading"><span class="eyebrow">ACCOUNT & SECURITY</span><h2>账户与安全</h2><p>注册、订阅与代退使用同一套登录信息，修改后统一生效。</p></div>
   <div class="profile-security-row"><span class="icon-tile"><KeyRound :size="20"/></span><div><h3>登录密码</h3><p>修改密码后，所有已登录设备都需要重新登录。</p></div><button class="btn secondary" @click="open('password')">修改密码<ArrowRight :size="15"/></button></div>
   <div v-if="enabled('keys')" class="profile-security-row"><span class="icon-tile"><ShieldCheck :size="20"/></span><div><h3>API 访问密钥</h3><p>查看和撤销密钥，管理你自己的服务端接入。</p></div><a class="btn secondary" href="#/keys">管理密钥<ArrowUpRight :size="15"/></a></div>
   <div class="profile-security-row"><span class="icon-tile"><LogOut :size="20"/></span><div><h3>退出当前登录</h3><p>在共享设备上使用后，及时退出以保护账户资料。</p></div><button class="text-btn danger-text" @click="emit('logout')">退出登录<LogOut :size="15"/></button></div>
  </section>
  <Modal v-if="modal" :title="modal==='name'?'编辑称呼':'修改登录密码'" @close="close"><form @submit.prevent="save"><div v-if="formError" class="alert danger" role="alert">{{formError}}</div><template v-if="modal==='name'"><label>账户邮箱<input readonly :value="user.email"/></label><label>称呼<input v-model="name" required maxlength="80" autocomplete="nickname"/></label></template><template v-else><p class="muted small profile-password-note">使用至少 10 位的新密码，更新后需要重新登录。</p><label>当前密码<input v-model="oldPassword" type="password" required autocomplete="current-password"/></label><label>新密码<input v-model="newPassword" type="password" required minlength="10" maxlength="72" autocomplete="new-password"/></label><label>确认新密码<input v-model="confirmPassword" type="password" required minlength="10" maxlength="72" autocomplete="new-password"/></label></template><button class="btn primary full" :disabled="busy">{{busy?'保存中…':'保存修改'}}<Check :size="16"/></button></form></Modal>
 </div>
</template>

<style scoped>
.profile-fact strong{font-size:23px}.profile-security{padding:28px}.profile-security-heading h2{font-size:20px;margin:8px 0}.profile-security-heading p{font-size:12px;color:var(--muted);line-height:1.8;margin-bottom:22px}.profile-security-row{display:flex;gap:16px;align-items:center;padding:22px 0;border-top:1px solid var(--line)}.profile-security-row>div{flex:1;min-width:0}.profile-security-row h3{font-size:14px;margin-bottom:7px}.profile-security-row p{font-size:12px;color:var(--muted);line-height:1.8}.profile-security-row>.btn,.profile-security-row>.text-btn{white-space:nowrap;flex-shrink:0}.profile-security-row:last-child{padding-bottom:0}@media(max-width:700px){.profile-security{padding:20px}.profile-security-row{flex-wrap:wrap;gap:12px}.profile-security-row>div{flex-basis:calc(100% - 62px)}.profile-security-row>.btn,.profile-security-row>.text-btn{margin-left:54px}.profile-fact strong{font-size:19px}}
</style>
