"""One IP policy for the unified web and public API entrypoints."""
import math
from . import db
from .common import BusinessError, config, event, ip

DEFAULTS = {
    'edge_per_minute': 600, 'login_per_minute': 10, 'register_per_minute': 5,
    'reset_per_minute': 10, 'api_per_minute': 120, 'order_per_minute': 5,
    'redeem_per_minute': 10, 'inspect_per_minute': 20, 'export_per_minute': 20,
    'slider_per_minute': 30, 'slider_verify_per_minute': 60,
    'email_per_minute': 5, 'email_per_day': 100, 'email_cooldown_seconds': 60,
}
RANGES = {key: (1, 100000 if key == 'edge_per_minute' else 10000) for key in DEFAULTS}
RANGES['email_cooldown_seconds'] = (10, 3600)


def administrator(request):
    user = getattr(request.state, 'user', None) or getattr(request.state, 'verified_identity', None)
    return bool(user and user.get('role') == 'admin' and user.get('status') == 'active')


def settings(request=None):
    if request is not None and hasattr(request.state, 'risk_settings'):
        return request.state.risk_settings
    value = {**DEFAULTS, **config('risk')}
    if request is not None: request.state.risk_settings = value
    return value


def enforce(request, category, setting, seconds=60):
    if administrator(request): return
    checked = getattr(request.state, 'rate_categories', set())
    if category in checked: return
    checked.add(category); request.state.rate_categories = checked
    maximum = settings(request)[setting]
    row = db.one("""INSERT INTO rate_buckets(key,hits,expires_at)
      VALUES($1,1,now()+$2*interval '1 second') ON CONFLICT(key) DO UPDATE SET
      hits=CASE WHEN rate_buckets.expires_at<=now() THEN 1 ELSE rate_buckets.hits+1 END,
      expires_at=CASE WHEN rate_buckets.expires_at<=now() THEN now()+$2*interval '1 second' ELSE rate_buckets.expires_at END
      RETURNING hits,extract(epoch FROM expires_at-now()) AS remaining""", category+':'+ip(request), seconds)
    if row['hits'] > maximum:
        event(request, 'rate_limit', 'medium', 'IP 请求频率超限：'+category)
        error = BusinessError('请求过于频繁，请稍后重试', 429)
        error.retry_after = max(1, math.ceil(row['remaining']))
        raise error
