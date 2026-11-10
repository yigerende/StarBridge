<script setup lang="ts">
import {computed,onMounted,ref} from 'vue'
import {ArrowRight,CheckCircle2,LockKeyhole,Upload,RotateCcw,ShieldCheck,UserRound,Users,Crown} from 'lucide-vue-next'
import {api,cash,type Row} from '../lib'
import {parseAccounts} from '../refund/import'
const props=defineProps<{user:Row}>(),emit=defineEmits<{refresh:[]}>()
const quote=ref<Row>(),mode=ref('single'),email=ref(''),password=ref(''),totp=ref(''),raw=ref(''),authorized=ref(false),busy=ref(false),error=ref(''),created=ref<string[]>([])
let fingerprint='',key=''
const parsed=computed(()=>{try{return {accounts:parseAccounts(raw.value),error:''}}catch(e){return {accounts:[],error:(e as Error).message}}})
const member=computed(()=>quote.value?.membership||props.user.membership)
const exclusive=computed(()=>['account_override','product_override'].includes(quote.value?.price_source)||member.value?.fee_override!=null)
const available=computed(()=>Number(props.user.balance||0)-Number(props.user.frozen||0))
const count=computed(()=>mode.value==='single'?1:parsed.value.accounts.length),total=computed(()=>count.value*(quote.value?.fee||0))
async function load(){try{quote.value=await api('/refund/quote')}catch(e){error.value=(e as Error).message}}
async function file(event:Event){const input=event.target as HTMLInputElement,f=input.files?.[0];input.value='';if(!f)return;if(f.size>1048576){error.value='文件不能超过 1 MB';return}raw.value=await f.text()}
async function submit(){
 busy.value=true;error.value=''
 try{
  const accounts=mode.value==='single'?[{email:email.value.trim(),password:password.value,totp_secret:totp.value.trim()}]:parsed.value.accounts
  if(!authorized.value||!accounts.length)throw Error('请填写账号资料并确认授权')
  const value=JSON.stringify(accounts);if(value!==fingerprint){fingerprint=value;key=crypto.randomUUID()}
  const result=await api('/refund/orders','POST',{accounts,authorized:true,idempotency_key:key})
  created.value=result.ids;password.value='';totp.value='';raw.value='';fingerprint='';key='';authorized.value=false;emit('refresh');await load()
 }catch(e){error.value=(e as Error).message}finally{busy.value=false}
}
onMounted(load)
</script>
<template>
 <div class="page-heading"><div><span class="eyebrow">REFUND SERVICE</span><h1>代退办理</h1><p>提交账号，统一跟进办理进度与服务费结算。</p></div><a href="#/orders?business=refund" class="btn secondary">查看代退订单<ArrowRight :size="16"/></a></div>
 <div v-if="created.length" class="panel refund-result" role="status"><CheckCircle2/><div><b>已受理 {{created.length}} 笔申请</b><p>服务费已冻结，办理完成后结算。账号敏感资料已从此表单清除。</p></div><a class="btn primary" :href="'#/orders?business=refund&order_id='+created[0]">查看进度</a></div>
 <p v-if="error" class="refund-error" role="alert">{{error}}</p>
 <div class="refund-layout">
  <section class="panel refund-form">
   <header class="refund-form-heading"><div class="refund-heading-icon"><RotateCcw :size="22"/></div><div><h2>提交代退申请</h2><p>填写需要办理的账号资料，支持单个或批量提交。</p></div><span class="refund-secure"><LockKeyhole :size="13"/>加密保护</span></header>
   <form @submit.prevent="submit"><fieldset :disabled="busy">
    <div class="refund-mode" role="group" aria-label="办理方式"><button type="button" :aria-pressed="mode==='single'" :class="{active:mode==='single'}" @click="mode='single'"><UserRound :size="16"/>单个办理</button><button type="button" :aria-pressed="mode==='batch'" :class="{active:mode==='batch'}" @click="mode='batch'"><Users :size="16"/>批量办理</button></div>
    <div v-if="mode==='single'" class="refund-fields">
     <label class="refund-email">账号邮箱<input v-model="email" type="email" autocomplete="off" maxlength="254" placeholder="请输入账号登录邮箱" required/></label>
     <label>账号密码<input v-model="password" type="password" autocomplete="new-password" maxlength="1024" placeholder="请输入账号密码" required/></label>
     <label>2FA 密钥<input v-model="totp" aria-label="2FA 密钥" aria-describedby="refund-totp-help" type="password" autocomplete="off" placeholder="Base32 密钥或 otpauth 链接" required/><small id="refund-totp-help">填写验证器密钥，而非六位动态验证码。</small></label>
    </div>
    <div v-else class="refund-batch">
     <div class="refund-import-heading"><span>每行一个账号，最多 500 条</span><label class="refund-import"><Upload :size="15"/>导入 TXT / CSV<input type="file" accept=".txt,.csv" aria-label="导入账号文件" @change="file"/></label></div>
     <label>账号资料<textarea v-model="raw" rows="7" spellcheck="false" autocomplete="off" placeholder="邮箱----密码----2FA 密钥&#10;也支持 CSV 或表格三列粘贴" required/></label>
     <div class="refund-import-hint"><span>文件上限 1 MB</span><b>已识别 {{count}} 个账号</b></div><p v-if="parsed.error" class="refund-error">{{parsed.error}}</p>
    </div>
    <label class="refund-consent"><input v-model="authorized" type="checkbox" required/><span><b>我已获得账号授权，并同意办理</b><small>我拥有这些账号或已取得持有人的明确授权。办理会变更订阅状态；服务完成不代表退款到账，实际退款以服务提供方为准。</small></span></label>
    <div class="refund-submit"><span><ShieldCheck :size="16"/>先冻结服务费，完成后结算</span><button class="btn primary" :disabled="!quote?.accepting||busy||!authorized||(mode==='batch'&&(!count||!!parsed.error))">{{busy?'正在提交…':!quote?'正在读取费用…':!quote.accepting?'代退办理暂未开放':'确认提交'}}<ArrowRight :size="16"/></button></div>
   </fieldset></form>
  </section>
  <aside class="refund-aside">
   <section class="panel refund-price"><header><h2>费用确认</h2><span>按账号计费</span></header>
    <div class="refund-member"><div><Crown :size="17"/><b>{{member?.current?.name||'当前会员'}}</b><a href="#/membership">会员权益<ArrowRight :size="13"/></a></div><p v-if="exclusive">已使用账户专属服务费</p><p v-else-if="member?.current?.discount<100">代退服务费享 {{Number((member.current.discount/10).toFixed(2))}} 折优惠</p><p v-else>代退支持会员减免，当前等级按标准服务费计价。</p></div>
    <div class="refund-unit-price"><h2>本次服务费</h2><strong>{{quote?cash(quote.fee):'—'}}<small>积分 / 账号</small></strong></div>
    <dl><div><dt>标准服务费</dt><dd>{{quote?cash(quote.standard):'—'}} 积分</dd></div><div :class="{positive:quote?.saved>0}"><dt>{{exclusive?'专属价减免':'会员减免'}}</dt><dd>{{quote?.saved>0?'− ':''}}{{quote?cash(Math.max(0,quote.saved)):'—'}} 积分 / 个</dd></div><div><dt>申请数量</dt><dd>{{count}} 个</dd></div><div class="refund-total"><dt>本次冻结</dt><dd>{{quote?cash(total):'—'}} <small>积分</small></dd></div><div><dt>账户可用</dt><dd>{{cash(available)}} 积分</dd></div></dl>
    <p v-if="quote&&!quote.accepting" class="refund-notice">当前暂未开放受理，请稍后再试。</p><p v-else-if="quote&&!quote.executing" class="refund-notice">当前可提交申请，等待队列恢复后办理。</p>
   </section>
   <section class="refund-guide"><h3>服务费如何处理</h3><p><LockKeyhole :size="16"/><span>提交申请时冻结服务费</span></p><p><CheckCircle2 :size="16"/><span>服务完成后结算一次</span></p><p><RotateCcw :size="16"/><span>失败或排队取消后释放</span></p><small>结果待核实时保持冻结，可在订单中心跟进。仅排队中的申请支持取消。</small></section>
  </aside>
 </div>
</template>
<style scoped>
.refund-layout{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:24px;align-items:start}.refund-form{overflow:hidden}.refund-form-heading{display:flex;align-items:center;gap:14px;padding:26px 28px;border-bottom:1px solid var(--line)}.refund-heading-icon{display:grid;place-items:center;background:#edf5f1;color:var(--green);width:46px;height:46px;border-radius:12px;flex:none}.refund-form h2,.refund-price>header h2{font-size:17px;margin:0}.refund-form-heading p{font-size:12px;color:var(--muted);margin:7px 0 0;line-height:1.7}.refund-secure{display:flex;align-items:center;gap:6px;margin-left:auto;color:var(--muted);font-size:11px;white-space:nowrap}.refund-form fieldset{border:0;padding:26px 28px 0;margin:0;min-width:0}.refund-mode{display:flex;gap:4px;background:#f2f5f4;padding:4px;border-radius:9px;width:fit-content;margin-bottom:26px}.refund-mode button{display:flex;gap:8px;align-items:center;border:0;background:transparent;color:var(--muted);padding:10px 20px;border-radius:6px;font:inherit;font-size:12px;cursor:pointer}.refund-mode button.active{background:#fff;color:var(--ink);box-shadow:0 1px 4px #203b2910;font-weight:600}.refund-fields{display:grid;grid-template-columns:1fr 1fr;gap:22px 20px}.refund-form label{margin:0;gap:9px;font-size:12px}.refund-email{grid-column:1/-1}.refund-fields input{height:44px}.refund-fields small{font-size:11px;color:var(--muted);font-weight:400;line-height:1.6}.refund-form textarea{resize:vertical;min-height:176px;font-size:12px;line-height:1.9}.refund-form .refund-consent{display:flex;flex-direction:row;align-items:flex-start;gap:11px;padding:17px 18px;margin:26px 0;background:#f7f9fa;border:1px solid var(--line);border-radius:9px;cursor:pointer}.refund-consent input{width:16px;height:16px;min-width:16px;flex:none;margin:3px 0 0;accent-color:var(--green)}.refund-consent b{font-size:12px;font-weight:550;line-height:1.8}.refund-consent small{display:block;font-size:11px;color:var(--muted);font-weight:400;line-height:1.8;margin-top:5px}.refund-submit{display:flex;align-items:center;justify-content:space-between;gap:16px;border-top:1px solid var(--line);padding:20px 0}.refund-submit>span{display:flex;align-items:center;gap:7px;font-size:11px;color:var(--muted)}.refund-submit .btn{min-height:42px;min-width:132px}.refund-price{padding:24px}.refund-price>header{display:flex;align-items:center;justify-content:space-between}.refund-price>header>span{font-size:11px;color:var(--muted)}.refund-member{margin:22px 0;background:#eff6f2;border:1px solid #dceae2;border-radius:9px;padding:14px;color:#36634f}.refund-member>div{display:flex;align-items:center;gap:7px}.refund-member b{font-size:12px}.refund-member a{display:flex;align-items:center;gap:4px;font-size:10px;margin-left:auto;color:inherit;white-space:nowrap}.refund-member p{margin:9px 0 0;font-size:11px;line-height:1.7}.refund-unit-price h2{font-size:12px;color:var(--muted);font-weight:400;margin-bottom:10px}.refund-unit-price strong{font-size:32px;font-weight:600;letter-spacing:-1px;font-variant-numeric:tabular-nums}.refund-unit-price strong small{font-size:11px;font-weight:400;letter-spacing:0;margin-left:8px;color:var(--muted)}.refund-price dl{margin:20px 0 0}.refund-price dl>div{display:flex;justify-content:space-between;gap:12px;padding:10px 0;font-size:12px}.refund-price dd{margin:0;font-variant-numeric:tabular-nums}.refund-price dt{color:var(--muted)}.refund-price .positive,.refund-price .positive dt{color:var(--green)}.refund-price dl>.refund-total{border-top:1px solid var(--line);margin-top:10px;padding-top:18px;align-items:center}.refund-total dd{font-size:22px;font-weight:600}.refund-total small{font-size:11px;font-weight:400}.refund-notice{font-size:11px;color:var(--muted);line-height:1.8;background:#f6f8f9;padding:11px 13px;border-radius:7px;margin:16px 0 0}.refund-guide{padding:22px 8px 0}.refund-guide h3{font-size:12px;margin:0 0 15px}.refund-guide p{display:flex;align-items:center;gap:10px;font-size:12px;color:#627b78;margin:12px 0}.refund-guide small{display:block;margin-top:16px;font-size:11px;line-height:1.8;color:var(--muted)}.refund-error{color:#af382d;background:#fff2ef;padding:12px 16px;border-radius:8px;font-size:12px;line-height:1.8;margin-bottom:18px}.refund-result{padding:22px;display:flex;gap:16px;align-items:center;margin-bottom:22px}.refund-result>svg{color:var(--green);flex:none}.refund-result p{font-size:12px;color:var(--muted);line-height:1.8;margin-top:6px}.refund-result .btn{margin-left:auto}.refund-import-heading,.refund-import-hint{display:flex;justify-content:space-between;align-items:center;gap:10px;font-size:11px;color:var(--muted);margin-bottom:16px}.refund-import-hint{margin:10px 0 0}.refund-import-hint b{color:var(--green);font-weight:500}.refund-form .refund-import{position:relative;display:flex;flex-direction:row;gap:7px;align-items:center;border:1px solid var(--line);border-radius:7px;padding:9px 12px;color:var(--ink);cursor:pointer;font-size:11px}.refund-import input{position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer}.refund-import:focus-within{outline:2px solid var(--green);outline-offset:2px}
@media(min-width:1400px){.refund-layout{grid-template-columns:minmax(0,1fr) 360px}}
@media(max-width:1050px){.refund-layout{grid-template-columns:1fr}.refund-aside{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:24px}.refund-guide{padding:22px 0}}
@media(max-width:600px){.refund-layout{gap:18px}.refund-form-heading{padding:20px;align-items:flex-start}.refund-secure{display:none}.refund-form fieldset{padding:20px 20px 0}.refund-fields,.refund-aside{grid-template-columns:1fr}.refund-form .refund-consent{padding:14px}.refund-submit{flex-direction:column;align-items:stretch}.refund-submit>span{justify-content:center}.refund-submit .btn{justify-content:center}.refund-price{padding:20px}.refund-guide{padding:0 6px}.refund-result{flex-wrap:wrap}.refund-import-heading{align-items:flex-start}.refund-import{white-space:nowrap}.refund-mode{width:100%}.refund-mode button{flex:1;justify-content:center;padding:10px 12px}}
</style>
