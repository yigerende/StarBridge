"""Customer capabilities, enforced at the authenticated request boundary."""
from .common import config, require

FEATURES = ('home','register','accounts','subscribe','links','renewal','refund','orders','wallet','activity','keys','invites','membership','docs','support','profile')
NAVIGATION = [('home','工作台'),('register','注册办理'),('accounts','我的账号'),('subscribe','订阅办理'),('refund','代退办理'),('orders','订单中心'),('wallet','资金账户'),
              ('activity','办理总览'),('links','结账链接'),('renewal','续费管理'),('keys','开发接入'),('invites','伙伴计划'),
              ('membership','会员中心'),('docs','API 文档'),('support','联系客服'),('profile','个人设置')]


def navigation(user=None):
    flags=capabilities(user)
    return [{'id':key,'path':'/'+key,'label':label,'group':'registration' if key=='accounts' else 'main' if key in ('home','register','subscribe','refund','orders','wallet','activity') else 'tools'}
            for key,label in NAVIGATION if flags[key]]


def capabilities(user=None):
    values = config('features')
    flags = {name: values.get(name, True) is True for name in FEATURES}
    flags['invites'] = flags['invites'] and config('invites').get('enabled') is True
    return flags


def allowed(name, user=None):
    return capabilities(user).get(name, False)


def require_any(user, *names):
    flags = capabilities(user)
    require(any(flags.get(name) for name in names), '此功能暂未开放', 403)


def enforce(request, user):
    if request.url.path.rstrip('/') == '/api/invites':
        require_any(user, 'invites')
    path = request.url.path.rstrip('/')
    if path.startswith('/api/v1/'):
        # Disabling developer access also suspends previously issued API keys.
        require_any(user, 'keys')
        path = '/api/' + path[len('/api/v1/'):]
    for prefix in ('/api/cslive-go/', '/api/cslive-full/'):
        if path.startswith(prefix): path = '/api/' + path[len(prefix):]
    route = path.removeprefix('/api/').split('/')[0]
    required = {'overview': ('home',), 'wallet': ('wallet',), 'topups': ('wallet',),
                'keys': ('keys',), 'invites': ('invites',), 'membership': ('membership',),
                'renewals': ('renewal',), 'quote': ('subscribe', 'links'),
                'catalog': ('subscribe', 'links', 'membership'), 'ledger': ('wallet',),
                'order-updates': ('orders',), 'support': ('support',), 'activity': ('activity',)}
    if route in required: require_any(user, *required[route])
    if route == 'refund': require_any(user, 'orders' if '/orders' in path and not (request.method=='POST' and path.endswith('/orders')) else 'refund')
    if route == 'orders':
        require_any(user, *('subscribe', 'links') if request.method == 'POST' and path == '/api/orders' else ('orders',))
    if route == 'subscriptions': require_any(user, 'orders')
    if route == 'me' and request.method == 'PATCH': require_any(user, 'profile')


async def enforce_payload(request, user):
    path = request.url.path.rstrip('/')
    web_order_flows = {'/api/orders': 'gener_direct_pay', '/api/cslive-go/orders': 'cslive_go_direct_pay',
                       '/api/cslive-full/orders': 'cslive_full_go'}
    if request.method == 'POST' and path in web_order_flows:
        from .common import body
        from .product_availability import require_product_flow
        value = await body(request)
        if path!='/api/cslive-go/orders':
            require(not value.get('promo_code'), '当前流程不支持自定义优惠码')
        product_id = value.get('product_id')
        if isinstance(product_id, str):
            require_product_flow(product_id.strip(), web_order_flows[path])
        # Legacy executors keep their existing currency implementation, while
        # newly submitted regions must still be enabled by the administrator.
        if path!='/api/cslive-go/orders':
            from .regions import selected
            country=value.get('country') or 'PH'
            if isinstance(country,str):selected(country.strip().upper(),web_order_flows[path])
    order_paths = {*web_order_flows, '/api/v1/orders'}
    if request.method == 'POST' and (path in order_paths or path.endswith('/session/inspect')):
        from .common import body
        value = await body(request)
        if path in order_paths:
            require_any(user, 'subscribe' if value.get('workflow', 'link') == 'subscription' else 'links')
        elif value.get('workflow') in ('subscription', 'link', 'checkout', 'renewal'):
            require_any(user, {'subscription': 'subscribe', 'link': 'links', 'checkout': 'links', 'renewal': 'renewal'}[value['workflow']])
        else:
            require_any(user, 'subscribe', 'links', 'renewal')


def project_user(user):
    result = dict(user)
    flags = capabilities(user)
    if not flags['wallet']:
        for key in ('balance', 'frozen'): result.pop(key, None)
    if not flags['invites']: result.pop('invite_code', None)
    if not flags['membership']: result.pop('fee_override', None)
    return result
