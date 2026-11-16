"""Admin-only renewal cancellation, using the existing AT subscription channel."""
from . import db
from .common import BusinessError, audit, now, require
from .refund_mother_upstream import UpstreamError


def save(mother, value):
    require(db.execute("""UPDATE refund_mothers SET management_json=management_json||$2::jsonb
        WHERE id=$1 AND team_id=$3 AND secret=$4 AND deleted_at IS NULL""",
        mother['id'], {'renewal': value}, mother['team_id'], mother['secret']),
        '母号资料已改变，请重新查询续费状态', 409)
    return value


def snapshot(mother, client, previous):
    value = client.renewal(mother['email'])
    pending = previous.get('pending') is True and value['will_renew']
    status = 'checking' if pending else 'enabled' if value['will_renew'] else 'disabled' if value['active'] else 'no_subscription'
    messages = {'checking': '取消结果尚未确认，请稍后查询状态。', 'enabled': '自动续费已开启。',
                'disabled': '自动续费已取消，当前套餐保留到期。', 'no_subscription': '当前没有需要续费的有效订阅。'}
    return {**previous, **value, 'pending': pending, 'status': status, 'message': messages[status]}


def operate(id, request, cancel=False, team_id=None):
    # The same team lease is shared with refund execution and all mother writes.
    from .refund_mothers import mother_client
    with mother_client(id) as (mother, client):
        if cancel:
            require(team_id == mother['team_id'], '母号团队已改变，请刷新页面后操作', 409)
        previous = mother['management_json'].get('renewal') or {}
        if previous.get('team_id') != mother['team_id']:
            previous = {}
        value = save(mother, snapshot(mother, client, previous))
        if not cancel or not value['will_renew'] or value['pending']:
            return value

        # Persist before the only write; ambiguous responses are queried, never replayed.
        value.update(pending=True, status='checking', submitted_at=now().isoformat(),
                     message='已提交取消，正在核实续费状态。')
        with db.transaction() as q:
            require(q.execute("""UPDATE refund_mothers SET management_json=management_json||$2::jsonb
                WHERE id=$1 AND team_id=$3 AND secret=$4 AND deleted_at IS NULL""",
                mother['id'], {'renewal': value}, mother['team_id'], mother['secret']),
                '母号资料已改变，请刷新页面后操作', 409)
            audit(q, request, 'refund.mother.renewal.cancel', id, '提交取消母号自动续费')
        rejected = None
        try:
            client.request('POST', '/subscriptions/cancel', {'account_id': mother['team_id']})
        except UpstreamError as exc:
            # Explicit rejection permits a later manual attempt; server/network failures do not.
            if exc.http_status in (400, 401, 403, 404, 422, 429):
                rejected = exc.http_status
        except BusinessError:
            # Lease loss or malformed response may follow an accepted upstream mutation.
            pass
        try:
            result = snapshot(mother, client, value)
        except BusinessError:
            result = {**value, 'message': '暂时无法核实取消结果，请稍后查询状态。'}
        if rejected and result['will_renew']:
            result.update(pending=False, status='failed', message=f'取消请求被拒绝（HTTP {rejected}），请检查母号凭据和权限后重试。')
        result = save(mother, result)
        with db.transaction() as q:
            audit(q, request, 'refund.mother.renewal.result', id, result['message'])
        return result
