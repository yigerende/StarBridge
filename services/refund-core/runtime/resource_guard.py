"""Seat checks and scoped cleanup. No child login, submission or wallet actions."""
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote


def parallel(client, operations):
    # curl sessions are not shared across threads. Each independent GET owns one.
    def run(operation):
        other = type(client)(client.job)
        other.child_id = client.child_id
        try:
            return operation(other)
        finally:
            other.session.close()
    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        return list(pool.map(run, operations))


def capacity(client):
    from refund_runner import UpstreamFailure
    try:
        data = client.call('GET', '/__capacity').json()
        rows = data['seat_capacity']
        matches = [r for r in rows if isinstance(r, dict) and r.get('type') == client.mother['seat_type']]
        if len(matches) != 1 or any(type(matches[0].get(k)) is not int or matches[0][k] < 0 for k in ('paid', 'held', 'available')):
            raise ValueError()
        if matches[0]['held'] > matches[0]['paid']:
            raise ValueError()
        return matches[0]['available']
    except (KeyError, TypeError, ValueError):
        raise UpstreamFailure('mother_shape') from None


def snapshot(client):
    remaining, members, invites = parallel(client, [capacity, lambda c: c.find('users', all_rows=True), lambda c: c.find('invites', all_rows=True)])
    return remaining, members, invites


def matches(client, row):
    return str(row.get('email') or row.get('email_address') or '').casefold() == client.email or bool(client.child_id and client.child_id in {row.get(k) for k in ('id', 'user_id', 'chatgpt_user_id')})


def check(client, checkpoint, during_login=False):
    from refund_runner import UpstreamFailure
    checkpoint('mother_check_started')
    remaining, members, invites = snapshot(client)
    own = any(matches(client, row) for row in members)
    if remaining < 1 and not own:
        checkpoint('mother_check_full')
        # Every mutation is authorized against current DB reservations, not only
        # a roster snapshot. Owners/admins and the current customer are protected.
        for kind, rows in [('users', members), ('invites', invites)]:
            for row in rows:
                email = str(row.get('email') or row.get('email_address') or '').casefold()
                identity = str(row.get('user_id') or row.get('id') or '')
                if (matches(client, row) or not email or not identity or email == client.mother['email'].casefold()
                        or row.get('role') not in {'standard-user', 'member', 'user'}):
                    continue
                checkpoint('resource_remove', {'target_email': email, 'target_id': identity, 'target_kind': kind})
                if kind == 'users':
                    client.call('DELETE', '/users/' + quote(identity, safe=''))
                else:
                    client.call('DELETE', '/invites', {'email_address': email})
        remaining, members, invites = snapshot(client)
        own = any(matches(client, row) for row in members)
        if remaining < 1 and not own:
            raise UpstreamFailure('mother_capacity', 409, operation='capacity')
    client._checked_rows = {'users': members, 'invites': invites}
    checkpoint('mother_check_completed')
    return remaining


def confirm_removed(client):
    from refund_runner import UpstreamFailure
    members, invites = parallel(client, [lambda c: c.find('users'), lambda c: c.find('invites')])
    if members is not None or invites is not None:
        raise UpstreamFailure('cleanup_pending')
