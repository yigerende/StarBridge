<script setup lang="ts">
import {computed,nextTick,onMounted,ref} from 'vue'
import {ArrowUpRight,Copy,Download,Menu,X} from 'lucide-vue-next'
import {api,copy,action,type Row} from '../lib'
import {schemaFields,fieldType,fieldRules,requestExamples,responseExamples,documentedOperations,codeSample} from '../apiDocs'
defineProps<{site?:Row}>()
const spec=ref<Row>({}),error=ref(''),lang=ref('curl'),mobile=ref(false),origin=location.origin
const selected=ref<Record<string,string>>({})
const operations=computed(()=>documentedOperations(spec.value))
const groups=computed(()=>((spec.value.tags||[]) as Row[]).map(tag=>({name:String(tag.name),items:operations.value.filter(x=>x.op.tags?.includes(tag.name))})))
const states:Row={draft:'已保存',queued:'等待办理',processing:'正在办理',awaiting_payment:'结账链接已生成，等待用户自行付款',succeeded:'付款成功，请继续确认订阅与续费结果',failed:'办理失败，查看进度说明',cancelled:'已停止',review:'结果待核实，请勿重复付款'}
const renewalStates:Row={queued:'等待办理',processing:'正在处理',checking:'正在确认关闭结果',disabled:'已确认关闭自动续费',no_subscription:'未发现可关闭的订阅',review:'待核实，请查看 message 或联系支持'}
const fields=(v:Row)=>schemaFields(spec.value,v)
const sample=(x:Row,key?:string)=>codeSample(x,origin,lang.value,key||selected.value[x.id])
const operation=(id:string)=>operations.value.find(x=>x.id===id)!
const successfulResponses=(op:Row)=>Object.fromEntries(Object.entries(op.responses||{}).filter(([code])=>code.startsWith('2')))
const errorResponses=(op:Row)=>Object.entries(op.responses||{}).filter(([code])=>!code.startsWith('2'))
onMounted(async()=>{try{spec.value=await api('/openapi.json');await nextTick();if(location.pathname==='/docs')document.getElementById(location.hash.slice(1))?.scrollIntoView()}catch(e){error.value=(e as Error).message}})
</script>

<template>
<div class="api-docs">
  <button class="docs-menu-toggle icon-btn bordered" @click="mobile=!mobile" aria-label="文档菜单"><Menu :size="20"/></button>
  <aside class="docs-sidebar" :class="{open:mobile}">
    <a href="/#/home" class="brand"><span class="brand-icon"><img :src="site?.icon_url||'/site-icon'" alt=""/></span><b>{{site?.name||'星桥'}}<small>DEVELOPERS</small></b></a>
    <button class="icon-btn docs-close" @click="mobile=false" aria-label="收起文档菜单"><X/></button>
    <p class="eyebrow">接入指引</p>
    <a href="/docs#start" @click="mobile=false">快速开始</a><a href="/docs#auth" @click="mobile=false">鉴权与数据约定</a><a href="/docs#availability" @click="mobile=false">可用功能与权限</a>
    <template v-for="guide in spec['x-guides']" :key="guide.id">
      <a v-if="guide.id==='registration'" href="/docs#registration" @click="mobile=false">注册与账号交付</a>
      <template v-if="guide.id==='subscribe'"><a href="/docs#product-ids" @click="mobile=false">订阅套餐标识</a><a href="/docs#input-sources" @click="mobile=false">订阅字段来源与调用顺序</a></template>
      <a :href="'/docs#guide-'+guide.id" @click="mobile=false">{{guide.title}}示例</a>
    </template>
    <a href="/docs#results" @click="mobile=false">如何判断办理结果</a>
    <template v-for="group in groups" :key="group.name"><p class="eyebrow">{{group.name}}</p>
      <a v-for="x in group.items" :key="x.id" :href="'/docs#'+x.id" @click="mobile=false"><span :class="['method',x.method]">{{x.method.toUpperCase()}}</span>{{x.op.summary}}</a>
    </template>
    <p class="eyebrow">参考</p><a href="/docs#fields" @click="mobile=false">字段字典</a><a href="/docs#states" @click="mobile=false">状态、分页与重试</a><a href="/docs#errors" @click="mobile=false">错误处理</a>
    <a href="/api/openapi.json" download="starbridge-openapi.json"><Download :size="14"/>下载 OpenAPI 3.1</a>
    <a href="/#/keys">管理 API 密钥<ArrowUpRight :size="14"/></a>
  </aside>
  <main class="docs-main">
    <div v-if="error" class="alert danger">{{error}}</div>
    <section id="start" class="docs-intro">
      <span class="eyebrow">星桥 API · {{spec.info?.version}}</span><h1>API 接入文档</h1>
      <p>供星桥用户从自己的服务端接入注册与账号交付、订阅办理、结账链接、关闭自动续费、代退办理及统一钱包。</p>
      <div class="docs-base"><code>{{origin}}</code><button class="text-btn" @click="action(()=>copy(origin))"><Copy :size="15"/>复制基础地址</button></div>
      <div class="docs-steps"><span>01 在开发接入创建密钥</span><span>02 查询套餐与余额</span><span>03 提交并保存任务编号</span><span>04 查询进度与结果</span></div>
      <div class="callout">先将 API 密钥保存为服务端环境变量 STARBRIDGE_API_KEY。示例中的 Session、银行卡和记录编号都是占位符，请替换为本次授权操作的真实资料。cURL 示例适用于 Bash；Windows 可使用 Python 或 Node.js 示例。</div>
      <div class="tabs"><button v-for="l in ['curl','node','python']" :key="l" :class="{active:lang===l}" @click="lang=l">{{({curl:'cURL',node:'Node.js',python:'Python'} as Row)[l]}}</button></div>
      <p>Python 示例使用 requests；Node.js 示例使用内置 fetch，建议 Node.js 22 或更新版本。</p>
    </section>
    <section id="auth" class="panel docs-section">
      <h2>鉴权与数据约定</h2><p>请求按来源 IP 限流，同一出口下的账号和密钥共用额度；注册、订阅和代退共用请求及下单限额。收到 429 时按 Retry-After 等待，重试下单须保留原请求标识。</p><p>请求携带 <code>Authorization: Bearer YOUR_API_KEY</code>。密钥只能操作所属星桥账户的数据；目标订阅账号由请求中的 Session 指定。可在开发接入中复制有效密钥、修改用途名称；撤销后立即失效。</p>
      <div class="docs-table"><table><thead><tr><th>接口范围</th><th>金额单位</th><th>可用余额</th></tr></thead><tbody><tr><td><code>/api/v1/</code></td><td>积分最小单位，100 = 1 积分</td><td><code>(balance − frozen) / 100</code> 积分</td></tr><tr><td><code>/registration/open/v1/</code></td><td>积分，最多两位小数</td><td><code>points</code> 已是可用积分，不再减 frozen</td></tr></tbody></table></div><p>两类接口共用同一账户和钱包。下单前核对金额单位，不要重复换算。</p>
      <p>套餐以 <code>GET /api/v1/catalog</code> 返回的 <code>items[].id</code> 为准，作为报价和下单的 <code>product_id</code>。Pro 20x 使用 <code>pro20x</code>；后续开放的新套餐同样传对应标识。</p><p>注册、订阅、代退分别计价。对应服务的专属价格优先，未设置时使用会员折扣；订阅先取套餐专属价，再取订阅默认专属价。专属价格不再叠加会员折扣。不参与会员减免的服务返回 <code>membership_eligible: false</code>；未设置专属价格时按标准价收费，<code>price_source</code> 为 <code>standard</code>。<code>price_source</code> 说明本次计价来源。专属价为 0 也有效。</p><p>会员按同一个累计成长值定级：已生效的等级调整计入 <code>membership.spent</code>，后续成功结算继续累计升级；<code>next</code> 和 <code>remaining</code> 对应当前等级的下一档。该数值用于会员等级，不代表钱包实际支出。</p><p>使用 HTTPS 从服务端调用；密钥、Session、银行卡及兑换码不要放入 URL、前端代码或日志。接口不开放跨域浏览器调用。</p>
      <p>Session 检查仅核对格式、账号与有效期，不验证上游登录和订阅状态。办理时请提供最新完整 Session，并保留其中的 sessionToken 或会话 Cookie，以便需要时刷新登录态。</p>
    </section>
    <section id="availability" class="panel docs-section">
      <h2>可用功能与权限</h2><p>通过 <code>GET /api/v1/me</code> 的 <code>features</code> 查看当前可用功能。所有 Bearer API 都要求 <code>keys=true</code>；对应功能不可用时返回 HTTP 403。请停止该功能的新请求，确认恢复后再调用，不要反复重试。</p>
      <div class="docs-table"><table><thead><tr><th>功能</th><th>features 字段</th><th>影响范围</th></tr></thead><tbody><tr v-for="item in spec['x-feature-access']" :key="item.feature"><td>{{item.service}}</td><td><code>{{item.feature}}</code></td><td>{{item.usage}}</td></tr></tbody></table></div>
      <p>例如 <code>links=false</code>、<code>subscribe=true</code> 时，只能提交 <code>workflow=subscription</code>，不能提交 <code>workflow=link</code>。共享的套餐、报价与 Session 检查按接口所列条件开放。</p><p>功能暂停不会取消已受理任务。订单查询仍可用时，继续用原编号查询；付款结果未知时不要创建新订单或再次付款。HTTP 403 表示本次请求未获准执行，不代表原订单失败或未扣款。</p>
    </section>
    <section id="registration" class="panel docs-section">
      <h2>注册与账号交付</h2>
      <p>使用开发接入创建的同一个 <code>xp_live_</code> 密钥；注册接口位于 <code>/registration/open/v1/</code>。此密钥只可操作所属账户的注册订单与交付账号。</p>
      <p>先查询注册套餐，再提交订单并保存订单编号。相同业务重试必须复用 <code>Idempotency-Key</code>，更改参数时使用新的键。按原订单查询进度，交付后查询账号或下载导出文件；下方列出完整接口、请求字段和示例。</p>
      <p>下单先冻结，账号资料完整交付后才扣费；选择 OAuth 时必须同时完成授权。失败释放对应冻结，部分成功仅扣已交付部分。<code>queued / running / oauth_pending</code> 为处理中，<code>review</code> 保留原单继续查询。</p>
      <p>账号资料在交付成功后保留 72 小时可领取。请及时读取或导出，截止时间查看 <code>download_expires_at</code>。导出接口返回 JSON 中的 <code>filename</code> 与 <code>content</code>；出现 <code>encoding: "base64"</code> 时先解码再保存文件。注册订单不提供用户取消接口。</p>
    </section>
    <section id="product-ids" class="panel docs-section">
      <h2>product_id 可以传什么？</h2>
      <p>传套餐的接口标识。下面是常见套餐对应值；实际可下单的套餐以 <a href="/docs#get_catalog">查询套餐接口</a> 返回的 <code>items[].id</code> 为准。</p>
      <div class="docs-table product-id-table"><table><thead><tr><th>套餐名称</th><th>product_id 传入值</th></tr></thead><tbody>
        <tr v-for="p in spec['x-product-examples']" :key="p.id"><td>{{p.name}}</td><td><code>{{p.id}}</code></td></tr>
      </tbody></table></div>
      <p>例如办理 Pro 20x，在下单 JSON 中填写 <code>"product_id": "pro20x"</code>；报价查询使用 <code>?product_id=pro20x</code>。</p>
      <p>获取最新列表：携带 API 密钥调用 <code>GET /api/v1/catalog</code>，找到所需套餐的 <code>name</code>，将同一条记录的 <code>id</code> 原样传入。后续新增套餐也使用这一方式。</p>
      <h3>Team团队订阅的席位与优惠码</h3>
      <p>Team 套餐使用 <code>product_id: "team"</code>。<code>seats</code> 是普通与高级席位的总数，至少 2、最多 100；<code>premium_seats</code> 是其中的高级席位数。省略时分别为 2 和 0。</p>
      <div class="docs-table"><table><thead><tr><th>席位组合</th><th>seats</th><th>premium_seats</th></tr></thead><tbody><tr><td>2 普通</td><td>2</td><td>0</td></tr><tr><td>1 普通＋1 高级</td><td>2</td><td>1</td></tr><tr><td>2 高级</td><td>2</td><td>2</td></tr></tbody></table></div>
      <p>可选 <code>promo_code</code> 填写用户自己的 Team 优惠码，留空则不使用。优惠码只影响上游结账金额，平台服务费仍以报价为准。订阅办理和仅生成结账链接均使用这些参数；总席位不足 2 或高级席位超过总数会返回 HTTP 400，且不会受理订单或冻结费用。</p>
    </section>
    <section id="input-sources" class="panel docs-section">
      <h2>字段来源与调用顺序</h2><p>订阅地区：从 <code>GET /api/v1/catalog</code> 的 <code>countries[]</code> 获取国家名称、<code>code</code> 和 <code>currency</code>。下单传 <code>country = code</code>，无需传代理或币种；国家列表会随开放配置更新。订单受理后保留下单时的地区和币种，实际订阅扣款以结账结果为准。</p>
      <p>先获取需要的值，再传给下一步接口。<code>items[].id</code> 表示列表中选中的那条记录的 id；<code>id</code> 表示创建响应最外层的 id。</p>
      <div class="docs-table"><table><thead><tr><th>需要填写的字段</th><th>从哪里获取</th><th>怎样传递</th></tr></thead><tbody>
        <tr v-for="entry in spec['x-input-sources']" :key="entry.field"><td><code>{{entry.field}}</code></td><td>{{entry.source}}<div class="field-source-links"><a v-for="link in entry.links" :key="link.operationId" :href="'/docs#'+link.operationId">{{link.label}}</a></div></td><td>{{entry.value}}</td></tr>
      </tbody></table></div>
      <p>其余输入字段的来源写在各接口参数表中。用户选择的套餐、地区、续费选项，以及银行卡、充值金额等资料，按字段说明填写；报价和账户查询返回的金额用于核对。</p>
    </section>
    <section v-for="guide in spec['x-guides']" :key="guide.id" :id="'guide-'+guide.id" class="panel docs-section">
      <h2>{{guide.title}}：完整调用步骤</h2><ol><li v-for="step in guide.steps" :key="step">{{step}}</li></ol>
      <p>提交前，将 <code>YOUR_UNIQUE_REQUEST_ID</code> 换成你为本次操作保存的唯一标识。相同操作重试时保留这个值。</p>
      <div class="section-heading"><h3>1. 提交申请</h3><button class="text-btn" @click="action(()=>copy(sample(operation(guide.create),guide.example)))"><Copy :size="14"/>复制示例</button></div>
      <pre>{{sample(operation(guide.create),guide.example)}}</pre>
      <details><summary>2. 使用{{guide.id==='refund'?'返回的每个订单编号':'返回的 id'}}查询结果</summary><p v-if="guide.id==='refund'">响应 ids 是订单编号数组。逐个将其中的编号替换到下面地址的 REFUND_ORDER_ID，查询各账号的办理结果。</p><p v-else>将下面地址中的编号占位符替换为创建响应的 id。查询不会重复提交或付款。</p><pre>{{sample(operation(guide.detail))}}</pre></details>
    </section>
    <section id="results" class="panel docs-section"><h2>如何判断办理结果</h2>
      <p>提交返回 HTTP 200 或 202 只表示申请已受理或返回原申请，不表示服务完成。查询返回 HTTP 200 只表示查询成功，请按业务检查结果字段。</p>
      <div class="docs-table"><table><thead><tr><th>要确认的结果</th><th>判断字段</th></tr></thead><tbody>
        <tr><td>注册账号交付</td><td><code>status = completed</code> 表示全部交付；<code>partial</code> 表示部分交付，按 <code>delivered</code> 领取对应账号。</td></tr>
        <tr><td>订阅付款成功</td><td><code>payment.state = succeeded</code></td></tr><tr><td>订阅生效</td><td><code>order.activation = active</code></td></tr>
        <tr><td>随订阅关闭续费</td><td><code>order.renewal = disabled</code>；kept 表示按要求保留。</td></tr>
        <tr><td>链接已生成</td><td><code>order.status = awaiting_payment</code> 且 <code>checkout.ready = true</code>，读取 checkout.url。</td></tr>
        <tr><td>独立关闭续费</td><td><code>task.status = disabled</code>；<code>no_subscription</code> 表示没有可关闭的订阅。</td></tr>
        <tr><td>代退服务完成</td><td><code>order.status = succeeded</code>，<code>order.fee_status = settled</code>；服务完成不表示银行退款已到账。</td></tr>
      </tbody></table></div><p>订阅付款为 <code>checking</code> 时继续查询原单，冻结积分保留，不要再次付款。付款成功后，生效或续费仍为 pending/retrying 时继续查询；review 时联系支持核实原订单。付款成功且 <code>activation=active</code> 表示订阅已生效；此时 <code>renewal=review</code> 只表示续费待确认，不影响本次订阅，不要重新付款。</p>
    </section>
    <section v-for="x in operations" :key="x.id" :id="x.id" class="panel docs-section">
      <div class="endpoint"><b :class="x.method">{{x.method.toUpperCase()}}</b><code>{{x.path}}</code></div>
      <h2>{{x.op.summary}}</h2><p>{{x.op.description}}</p>
      <p v-if="x.op['x-required-feature']" class="muted small">所需功能：<code>{{x.op['x-required-feature']}}</code>。所有调用同时要求 <code>keys=true</code>。</p>
      <p v-if="['get_catalog','get_quote','create_order'].includes(x.id)"><a href="/docs#product-ids">查看 product_id 可传值与套餐名称对照</a></p>
      <div v-if="x.op.parameters?.length" class="docs-table"><table><thead><tr><th>参数</th><th>位置</th><th>要求</th><th>说明与范围</th></tr></thead><tbody>
        <tr v-for="p in x.op.parameters" :key="p.name"><td><code>{{p.name}}</code></td><td>{{p.in}}</td><td>{{p.required?'必填':'可选'}}</td><td>{{p.description}}<div v-if="p['x-source-operations']?.length" class="field-source-links"><a v-for="link in p['x-source-operations']" :key="link.operationId" :href="'/docs#'+link.operationId">查看来源：{{link.label}}</a></div><span v-if="p.schema.default!==undefined"> 默认：{{p.schema.default}}。</span><span v-if="p.schema.minLength"> 长度：{{p.schema.minLength}}～{{p.schema.maxLength}}。</span><span v-if="p.schema.minimum!==undefined"> 范围：{{p.schema.minimum}}～{{p.schema.maximum}}。</span><span v-if="p.schema.enum"> 可选：{{p.schema.enum.join(' / ')}}。</span></td></tr>
      </tbody></table></div>
      <div v-if="x.op.requestBody" class="docs-table"><table><thead><tr><th>JSON 字段</th><th>类型</th><th>要求</th><th>说明与默认值</th></tr></thead><tbody>
        <tr v-for="f in fields(x.op.requestBody.content['application/json'].schema)" :key="f.name"><td><code>{{f.name}}</code></td><td>{{fieldType(f)}}</td><td>{{f.required?(f.nested?'提供父字段时必填':'必填'):'按说明填写'}}</td><td>{{f.description}}<div v-if="f['x-source-operations']?.length" class="field-source-links"><a v-for="link in f['x-source-operations']" :key="link.operationId" :href="'/docs#'+link.operationId">查看来源：{{link.label}}</a></div><small v-if="fieldRules(f)" class="muted">{{fieldRules(f)}}</small></td></tr>
      </tbody></table></div>
      <div class="section-heading">
        <div class="tabs"><button v-for="l in ['curl','node','python']" :key="l" :class="{active:lang===l}" @click="lang=l">{{({curl:'cURL',node:'Node.js',python:'Python'} as Row)[l]}}</button></div>
        <button class="text-btn" @click="action(()=>copy(sample(x)))"><Copy :size="14"/>复制示例</button>
      </div>
      <label v-if="Object.keys(requestExamples(x)).length>1">请求场景 <select :value="selected[x.id]||Object.keys(requestExamples(x))[0]" @change="selected[x.id]=($event.target as HTMLSelectElement).value"><option v-for="(e,k) in requestExamples(x)" :key="k" :value="k">{{e.summary}}</option></select></label>
      <pre>{{sample(x)}}</pre>
      <details><summary>成功响应字段与示例</summary>
        <div v-for="(response,code) in successfulResponses(x.op)" :key="code">
          <h4>HTTP {{code}} · {{(response as Row).description}}</h4>
          <div class="docs-table" v-if="fields((response as Row).content?.['application/json']?.schema).length"><table><thead><tr><th>字段</th><th>类型</th><th>说明</th></tr></thead><tbody>
            <tr v-for="f in fields((response as Row).content?.['application/json']?.schema)" :key="f.name"><td><code>{{f.name}}</code></td><td>{{fieldType(f)}}</td><td>{{f.description}}<small v-if="fieldRules(f)" class="muted">{{fieldRules(f)}}</small></td></tr>
          </tbody></table></div><div v-for="(example,key) in responseExamples(response as Row)" :key="key"><p v-if="example.summary" class="muted">{{example.summary}}</p><pre>{{JSON.stringify(example.value,null,2)}}</pre></div>
        </div>
      </details><details v-if="errorResponses(x.op).length"><summary>可能返回的错误</summary><p>通用处理规则见 <a href="/docs#errors">错误处理</a>。</p><ul><li v-for="[code,response] in errorResponses(x.op)" :key="code"><code>{{code}}</code> · {{(response as Row).description}}</li></ul></details>
    </section>
    <section id="fields" class="panel docs-section"><h2>字段字典</h2><p>点号表示对象内字段，[] 表示数组元素，{product_id} 表示以套餐标识为键的字典。所有时间为带时区的 ISO 8601 格式。可选字段可能省略；null 不等同于 0 或失败。</p>
      <details v-for="(s,name) in spec.components?.schemas" :key="name"><summary>{{name}}</summary><div class="docs-table"><table><thead><tr><th>字段</th><th>类型</th><th>说明</th></tr></thead><tbody>
        <tr v-for="f in fields(s)" :key="f.name"><td>{{f.name}}</td><td>{{fieldType(f)}}</td><td>{{f.description}}<small v-if="fieldRules(f)" class="muted">{{fieldRules(f)}}</small></td></tr>
      </tbody></table></div></details>
    </section>
    <section id="states" class="panel docs-section"><h2>状态、分页与重试</h2>
      <h3>注册订单</h3><div class="docs-table"><table><tbody><tr v-for="(label,state) in spec['x-registration-order-states']" :key="state"><td><code>{{state}}</code></td><td>{{label}}</td></tr></tbody></table></div><details><summary>注册订单中单个账号的状态</summary><div class="docs-table"><table><tbody><tr v-for="(label,state) in spec['x-registration-item-states']" :key="state"><td><code>{{state}}</code></td><td>{{label}}</td></tr></tbody></table></div></details>
      <h3>订阅与结账链接订单</h3><div class="docs-table"><table><tbody><tr v-for="(label,state) in states" :key="state"><td><code>{{state}}</code></td><td>{{label}}</td></tr></tbody></table></div>
      <h3>独立续费任务状态</h3><div class="docs-table"><table><tbody><tr v-for="(label,state) in renewalStates" :key="state"><td><code>{{state}}</code></td><td>{{label}}</td></tr></tbody></table></div>
      <h3>代退订单</h3><div class="docs-table"><table><tbody><tr v-for="(label,state) in spec['x-refund-order-states']" :key="state"><td><code>{{state}}</code></td><td>{{label}}</td></tr></tbody></table></div><p>代退使用 page / page_size（默认 50，上限 500），根据 total 判断翻页；费用状态 frozen、settled、released 分别表示冻结、结算和释放。响应未知时使用原 Idempotency-Key 和原账号资料查询或重试受理，不能换键重复申请；attention 请联系支持。</p>
      <p>注册与订阅建议每 5 秒查询，代退每 10～15 秒查询。订阅付款 review 时停止自动重付并联系支持；注册订单 review 时继续查询原单，等待最终结算。已结束的任务停止轮询。提链订单在取得链接后停止轮询。订阅已付款但后续仍 pending 时可继续查询，长时间未变化请联系支持。</p>
      <p>订阅及统一钱包列表使用 page 和 page_size，读取 has_more 决定是否继续下一页。订单和钱包默认每页 50 条，办理总览默认 25 条，上限均为 500；可选择 10 / 50 / 100 / 500 条。sort_by 选择表中列出的排序字段，sort_dir 为 asc 或 desc；筛选与排序均在完整记录上执行；有新记录写入时页码内容可能变化，汇总时按 id 去重。</p>
      <p>注册订单和账号列表使用 <code>limit</code>（默认 50，上限 500）与 <code>offset</code>（默认 0），依据 <code>total</code> 和已读取条数判断是否结束；下一页将 offset 增加本次读取条数。两类分页参数不能混用。</p>
      <h3>超时与重复提交</h3><p>创建订单、续费任务和充值申请必须携带 Idempotency-Key。提交前保存请求标识和内容；网络超时或 HTTP 500 后，使用相同标识和相同内容重试，返回已有编号时不会重复创建。换账号、套餐、金额或银行卡等内容，必须视为新操作并使用新标识。</p>
      <p>同一标识配合不同内容返回 409。查询和重复取消同一充值申请可安全重试。兑换余额超时先核对余额和流水；确认原订单结果前，不要用新标识反复下单。支付结果 review 时联系支持核实原单。</p>
      <p>订阅下单前参考 payment_connected，注册参考 orders_open，代退参考 accepting；最终以提交结果为准。queued 表示等待办理，无需重复提交。多个密钥共享所属账户的请求限额。</p>
    </section>
    <section id="errors" class="panel docs-section"><h2>错误处理</h2><p>错误返回 <code>{"error":"中文错误原因"}</code>。按 HTTP 状态决定处理方式，中文说明用于展示。</p>
      <div class="docs-table"><table><thead><tr><th>状态</th><th>处理方式</th></tr></thead><tbody>
        <tr v-for="(v,k) in {400:'核对参数类型、Session、余额及业务条件；修正后再提交',401:'检查密钥是否有效及账户状态',402:'注册可用积分不足，核对可用余额并充值',403:'当前功能未开放、密钥权限不足或请求来源不允许',404:'记录不存在或不属于本账户',409:'请求标识内容冲突或记录状态不允许操作，先核对原请求',413:'缩小请求体；总请求最多 1 MB，Session 最多 256 KB',429:'若有 Retry-After，按响应秒数等待；否则至少等待 60 秒。未完成订单达到上限时先等待原单结算',500:'查询可退避重试；创建任务使用原请求标识重试',503:'办理资源暂不可用，稍后使用原请求标识重试'}" :key="k"><td><code>{{k}}</code></td><td>{{v}}</td></tr>
      </tbody></table></div>
    </section>
  </main>
</div>
</template>

<style scoped>
.docs-section,.docs-intro{scroll-margin-top:24px}
.docs-section li{margin:9px 0;line-height:1.8}
.docs-intro .tabs{margin-top:22px}
.docs-section label{display:flex;gap:12px;align-items:center;margin:15px 0}
.docs-section select{max-width:260px}
.docs-section h3{margin-top:24px}
.docs-table td{overflow-wrap:anywhere}
.product-id-table table{min-width:0;width:100%}
.product-id-table code{white-space:nowrap}
.field-source-links{display:flex;flex-direction:column;gap:6px;margin-top:8px;font-size:12px}
.section-heading{flex-wrap:wrap;gap:12px}
@media(max-width:760px){.docs-table{overflow-x:auto}.docs-table table{min-width:580px}.product-id-table table{min-width:0}.docs-section pre,.docs-intro pre{max-width:100%;overflow:auto}}
</style>
