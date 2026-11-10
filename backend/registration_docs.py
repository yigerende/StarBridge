import json
from pathlib import Path


def document():
    directory=Path(__file__).resolve().parent
    result=json.loads((directory/'openapi.json').read_text(encoding='utf-8'))
    registration=json.loads((directory/'registration-openapi.json').read_text(encoding='utf-8'))
    # Customer wording and unified membership fields; the engine contract stays intact.
    registration['paths']['/open/v1/products']['get']['description']='查询标准注册及含 OAuth 服务的当前单价、会员减免和每单数量上限。下单使用 quantity 与 need_oauth，无需 product_id。'
    registration['paths']['/open/v1/products']['get']['responses']['200']['content']['application/json']['example']['products']=[{'name': '标准账号', 'need_oauth': False, 'unit_price': 50, 'standard_price': 50, 'saved': 0, 'price_source': 'membership'}, {'name': 'OAuth 账号', 'need_oauth': True, 'unit_price': 100, 'standard_price': 100, 'saved': 0, 'price_source': 'membership'}]
    registration['paths']['/open/v1/products']['get']['responses']['200']['content']['application/json']['example']['membership']={'current': {'code': 'L1', 'name': '会员', 'threshold': 0, 'discount': 100, 'description': ''}, 'source': 'automatic', 'until': None}
    registration['paths']['/open/v1/registration-regions']['get']['description']='查询可选地区代码和名称。下单传返回的 code；省略或传 random 表示随机地区。已受理订单保留下单时的选择。'
    registration['paths']['/open/v1/registration-orders']['post']['requestBody']['content']['application/json']['schema']['properties']['registration_region']['description']='可选注册地区；传地区查询返回的 code，例如 JP。省略或 random 表示随机。相同请求标识重试须保留原参数。'
    registration['paths']['/open/v1/accounts/{id}/credentials']['get']['description']='交付后 72 小时内读取密码、2FA、当前验证码和网页登录凭据；到期返回 404。access_token/session_token 未保存时为空或省略。含 OAuth 服务另返回 oauth；其 access_token 与顶层网页登录凭据用途不同。读取不会刷新凭据。'
    registration['paths']['/open/v1/accounts/export']['post']['description']='提交 1～1000 个交付账号 ID。不存在、无权访问或领取期已过时整批返回 404。txt/csv 为邮箱、密码、2FA 三列；json 包含账号凭据；email_at/email_session 为邮箱与对应网页登录凭据的 TXT，任一账号缺少凭据时整批返回 409。cpa/sub 仅支持含 OAuth 的账号；cpa 多账号返回 ZIP。响应为 JSON，按 filename 保存 content；encoding=base64 时先解码。重复 ID 会去重，输出顺序可能与请求不同。'
    registration['x-order-states']['review']='结果核实中，请继续查询原订单，不重复提交。'
    registration['x-errors']=[{'status': 400, 'message': '参数格式或业务条件不正确', 'action': '核对类型、数量上限、筛选值和必填参数，修正后提交。'}, {'status': 401, 'message': 'API Key 无效或账户不可用', 'action': '检查 Bearer 密钥、撤销状态及账户是否停用。网页登录会话不能代替 API Key。'}, {'status': 402, 'message': '可用积分不足', 'action': '查询账户 points 并充值；points 已是可用余额，不要再减 frozen。'}, {'status': 403, 'message': '权限不足或功能未开放', 'action': '检查当前账户可用功能，仍无法调用时联系服务支持。'}, {'status': 404, 'message': '记录不存在、不属于当前账户或账号资料已到期', 'action': '核对订单或账号 ID；账号交付满 72 小时后不可再读取或导出，请提前保存。'}, {'status': 409, 'message': '幂等键与请求内容冲突', 'action': '相同操作重试须复用相同键和内容；确认要创建新订单时使用新键。'}, {'status': 413, 'message': '请求体过大', 'action': '总请求体不能超过 1 MiB；账号导出每次最多 1000 个 ID。'}, {'status': 429, 'message': '请求过频或未完成订单达到上限', 'action': '有 Retry-After 时按该秒数等待；否则至少等待 60 秒。订单上限需等待已有订单结算，review 继续查询原订单，系统自动恢复或退款。'}, {'status': 500, 'message': '服务暂时不可用', 'action': '查询可退避重试；创建订单需复用原幂等键和原内容，避免重复预留积分。'}, {'status': 503, 'message': '服务或新订单暂停受理', 'action': '稍后重试；创建订单保留原幂等键。以响应说明判断暂停范围。'}]
    registration['components']['schemas']['RegistrationRegion']['properties']['code']['description']='注册地区代码；下单时原样传入，random 表示随机。'
    registration['components']['schemas']['Products']['properties']['membership']['properties']={'current': {'type': 'object', 'properties': {'code': {'type': 'string', 'description': '等级代码。'}, 'name': {'type': 'string', 'description': '等级名称。'}, 'threshold': {'type': 'integer', 'description': '累计消费门槛，单位：积分最小单位（展示时除以 100）。', 'minimum': 0}, 'discount': {'type': 'integer', 'description': '计费百分比，95 表示按标准服务费的 95% 收取。'}, 'description': {'type': 'string', 'description': '等级说明。'}}, 'description': '当前会员等级；threshold 使用积分最小单位，100 = 1 积分。'}, 'source': {'type': 'string', 'description': '会员等级来源。', 'enum': ['automatic', 'manual']}, 'until': {'type': ['string', 'null'], 'description': '当前指定会员等级截止时间；null 表示没有期限。', 'format': 'date-time'}}
    registration['components']['schemas']['Products']['properties']['membership']['description']='当前账户会员信息。'
    registration['components']['schemas']['Product']['properties']['standard_price']['description']='每个账号的标准服务费，单位为积分。'
    registration['components']['schemas']['Product']['properties']['saved']['description']='标准价减实际单价，单位为积分；专属价高于标准价时可能为负。'
    registration['components']['schemas']['Product']['properties']['price_source']['description']='本次单价来源。'
    registration['components']['schemas']['Order']['properties']['cancel_requested']['description']='是否已请求停止未交付部分；true 不代表所有办理已结束，继续查询最终状态。'
    registration['components']['schemas']['Account']['properties']['created_at']['description']='交付账号记录创建时间；未知时为 null。'
    registration['components']['schemas']['Account']['properties']['download_expires_at']['description']='领取截止时间；到期后账号不再出现在列表中，凭据及导出不可用。'
    registration['components']['schemas']['DeviceFingerprint']['properties']['registeredAt']['description']='账号注册时间，ISO 8601；未知时为空。'
    registration['components']['schemas']['StoredCredentials']['properties']['session_token']['description']='完整网页登录 Session Token，即登录 Cookie 值，不是 Session JSON；未保存时为空或省略。'
    registration['components']['schemas']['StoredCredentials']['properties']['oauth']['description']='含 OAuth 服务交付的授权资料；与网页登录凭据分开使用。'
    paths=registration['paths']
    paths['/open/v1/me']['get'].update(summary='查询注册接口账户视图',description='同一星桥账户在注册接口中的积分视图。points 已扣除冻结部分，可直接用于下单；frozen 为冻结积分。统一账户资料也可使用 GET /api/v1/me 查询，两者的金额单位不同，详见鉴权与数据约定。')
    paths['/open/v1/products']['get']['description']='查询标准注册与注册加 OAuth 的实际单价、标准价、会员减免和每单数量上限。下单通过 need_oauth 选择服务，不需要 product_id。价格示例仅供说明，请以实时查询结果为准。'
    paths['/open/v1/registration-regions']['get']['description']='返回当前可选的注册国家代码和名称。下单可传返回的 code，省略或传 random 表示随机选择。地区列表可能更新，请在创建新订单前查询；已受理订单保留下单时的地区设置。'
    paths['/open/v1/registration-orders']['post']['requestBody']['content']['application/json']['schema']['properties']['registration_region']['description']='省略或传 random 表示随机选择；指定地区时传地区查询接口返回的 code，例如 JP。同一订单重试须保持原参数。'
    paths['/open/v1/accounts/{id}/credentials']['get']['description']='交付成功后 72 小时内读取邮箱、密码、2FA、当前验证码和已保存的网页登录凭据；到期返回 404。读取不会刷新登录凭据。可选 access_token / session_token 缺失时为空或省略。含 OAuth 服务另返回 oauth，其 access_token 与顶层网页登录 access_token 用途不同。'
    registration['x-order-states']['review']='正在恢复或核实，继续查询原单；最终以交付和结算结果为准'
    registration['components']['schemas']['RegistrationRegion']['properties']['code']['description']='注册地区代码；将地区查询返回的 code 原样传入。random 表示随机选择。'
    registration['components']['schemas']['Order']['properties']['registration_region']['description']='下单选择的注册地区；random 表示随机，未指定记录可能为 null。'
    registration['components']['schemas']['StoredCredentials']['properties']['session_token']['description']='完整网页登录 Session Token（Cookie 值），不是 Session JSON。未保存时为空或省略，读取不触发刷新。'
    def adapt(value):
        if isinstance(value,list):return [adapt(item) for item in value]
        if isinstance(value,dict):
            if value.get('$ref','').startswith('#/components/responses/'):
                name=value['$ref'].split('/')[-1]
                return adapt({**registration['components']['responses'][name],**{k:v for k,v in value.items() if k!='$ref'}})
            output={key:adapt(item) for key,item in value.items()}
            if '$ref' in output and output['$ref'].startswith('#/components/'):
                category,name=output['$ref'].split('/')[-2:]
                output['$ref']=f'#/components/{category}/Registration{name}'
            if 'operationId' in output:output['operationId']='registration_'+output['operationId']
            if 'security' in output:output['security']=[{'BearerKey':[]}]
            if 'tags' in output:output['tags']=['注册 · '+tag for tag in output['tags']]
            output.pop('x-scope',None)
            return output
        return value
    result['paths'].update({'/registration'+path:adapt(value) for path,value in registration['paths'].items()})
    for category,values in registration['components'].items():
        if category!='securitySchemes':
            result['components'].setdefault(category,{}).update({'Registration'+name:adapt(value) for name,value in values.items()})
    result['tags'].extend({**tag,'name':'注册 · '+tag['name']} for tag in registration['tags'])
    result['info'].update(title='星桥用户开放 API',version='2.3.0',description='使用同一个 API 密钥接入注册与账号交付、订阅办理、代退办理及积分钱包。先查询服务与报价，提交后保存订单编号并查询办理结果。密钥只能访问所属账户的数据。')
    result['x-guides'].append({'id':'registration','title':'注册与账号交付','create':'registration_create_order','detail':'registration_get_order','example':'standard','steps':[
        '查询注册服务与价格、可选注册地区，并核对统一钱包的可用积分。',
        '提交 quantity、need_oauth 和可选 registration_region，保存 Idempotency-Key 与响应订单 id。新订单返回 HTTP 202，幂等重放返回 200。',
        '按订单 id 查询进度。queued、running、review 继续查询原单；completed、partial、failed、cancelled 为结束状态。',
        '使用订单 id 筛选交付账号，以账号 id 读取凭据或批量导出；在交付成功后的 72 小时领取期内保存。',
    ]})
    result['x-registration-order-states']=registration.get('x-order-states',{})
    result['x-registration-item-states']=registration.get('x-item-states',{})
    # Business order is shared with navigation: registration, subscription, refund.
    tag_order = ['注册 · 账户与服务', '注册 · 注册订单', '注册 · 账号交付',
                 '账户与套餐', '订阅与结账链接', '关闭自动续费', '账号检查', '代退办理', '资金与充值']
    rank = {name: index for index, name in enumerate(tag_order)}
    result['tags'].sort(key=lambda tag: rank.get(tag['name'], len(rank)))
    result['paths'] = dict(sorted(result['paths'].items(), key=lambda item: min(
        (rank.get(tag, len(rank)) for op in item[1].values() if isinstance(op, dict)
         for tag in op.get('tags', [])), default=len(rank))))
    guides = {'registration': 0, 'subscribe': 1, 'links': 2, 'renewal': 3, 'refund': 4}
    result['x-guides'].sort(key=lambda guide: guides.get(guide['id'], len(guides)))
    return result
