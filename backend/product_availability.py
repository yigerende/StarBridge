"""Availability of newly offered products; execution stays in its owning flow."""
from .common import require

GO_HTTP_FLOW = 'cslive_go_direct_pay'
FLOW_PRODUCTS = frozenset({'plus', 'promax'})


def available(product_id, flow):
    if flow == GO_HTTP_FLOW and product_id == 'team_premium':
        return False
    return product_id not in FLOW_PRODUCTS or flow == GO_HTTP_FLOW


def visible_products(rows, flow):
    return [display_product(row, flow) for row in rows if available(row['id'], flow)]


def admin_product(row, flow):
    supported = available(row['id'], flow)
    reason = ''
    if not supported:
        reason = ('当前流程通过“Team团队订阅”组合普通席位与高级席位。'
                  if row['id'] == 'team_premium' else
                  ('尚未选择订阅办理流程。' if not flow else '当前订阅办理流程不支持此套餐。')
                  + '请选择“Go提链+http直连付款流程 +Go付款成功后续”后展示。')
    return dict(display_product(row, flow), visible=bool(row['enabled'] and supported),
                availability_reason=reason)


def display_product(row, flow):
    if row['id'] == 'team' and flow == GO_HTTP_FLOW:
        row = dict(row, name='Team团队订阅')
        if 'description' in row:row['description']='按需组合普通席位与高级席位'
    return row


def require_product_flow(product_id, flow):
    require(available(product_id, flow), '当前办理流程未开放此套餐', 400)
