import json
from pathlib import Path


def document():
    directory=Path(__file__).resolve().parent
    result=json.loads((directory/'openapi.json').read_text(encoding='utf-8'))
    registration=json.loads((directory/'registration-openapi.json').read_text(encoding='utf-8'))
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
    result['info'].update(title='星桥用户开放 API',version='2.1.0',description='面向星桥用户的服务端接入文档，覆盖订阅、注册与账号交付、结账链接、关闭自动续费、账户及统一钱包。使用开发接入中创建的同一个 API 密钥，只可访问本账户的数据。功能是否开放以接口返回为准；不包含运营管理和内部执行接口。')
    result['x-guides'].append({'id':'registration','title':'注册与账号交付','create':'registration_create_order','detail':'registration_get_order','example':'standard','steps':[
        '查询注册服务与价格、可选注册地区，并核对统一钱包的可用积分。',
        '提交 quantity、need_oauth 和可选 registration_region，保存 Idempotency-Key 与响应订单 id。新订单返回 HTTP 202，幂等重放返回 200。',
        '按订单 id 查询进度。queued、running、review 继续查询原单；completed、partial、failed、cancelled 为结束状态。',
        '使用订单 id 筛选交付账号，以账号 id 读取凭据或批量导出；在交付成功后的 72 小时领取期内保存。',
    ]})
    result['x-registration-order-states']=registration.get('x-order-states',{})
    result['x-registration-item-states']=registration.get('x-item-states',{})
    return result
