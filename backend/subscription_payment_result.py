"""Read the original transaction after submission; never replay a charge here."""
import logging
import time

from . import payment_trace as trace

log = logging.getLogger(__name__)


def _object(value):
    return value if isinstance(value, dict) else {}


def classify(data):
    """Return paid, explicit decline code, and the actual amount received."""
    data = _object(data)
    attempt = _object(data.get('submission_attempt'))
    intents = [_object(container.get('payment_intent')) for container in
               (data, attempt, _object(data.get('error')))]
    # A successful intent wins over stale submission-attempt errors.
    intent = next((item for item in intents if item.get('status') == 'succeeded'),
                  next((item for item in intents if item), {}))
    state = str(intent.get('status') or '').lower()
    amount = intent.get('amount_received')
    amount = amount if type(amount) is int and amount > 0 else 0
    if data.get('payment_status') == 'paid' or state == 'succeeded':
        return True, '', amount
    # A completed checkout or a failed HTTP request alone is not proof of payment.
    if state in ('processing', 'requires_action', 'requires_capture', 'requires_confirmation'):
        return False, '', 0
    error = _object(intent.get('last_payment_error'))
    declined = (state in ('canceled', 'cancelled') or
                state == 'requires_payment_method' and
                (error.get('decline_code') or error.get('code') == 'card_declined'))
    if declined:
        return False, str(error.get('decline_code') or error.get('code') or 'card_declined'), 0
    return False, '', 0


def confirm_once(stripe, checkout_id, form):
    """A failed response can hide a successful charge. Continue with GET only."""
    try:
        result = stripe.confirm(checkout_id, form)
        return _object(result)
    except Exception as exc:
        log.warning('Payment confirmation response unavailable: %s', trace.error_code(exc))
        trace.record('checking', 'running', 'payment_verifying')
        return {}


def poll_result(stripe, checkout_id, public_key):
    """Bounded reads on the same checkout, HTTP session and order proxy."""
    latest = None
    for attempt in range(5):
        if attempt:
            time.sleep(1.5)
        try:
            response = trace.http_call(
                'payment_read', stripe.session.get,
                f'https://api.stripe.com/v1/payment_pages/{checkout_id}',
                params={'key': public_key, 'eid': 'NA'}, timeout=30,
            )
            if response.status_code != 200:
                continue
            latest = response.json()
        except Exception as exc:
            # Even a runtime/JSON/network error on a read must not skip later reads.
            log.warning('Payment result read unavailable: %s', trace.error_code(exc))
            continue
        paid, declined, amount = classify(latest)
        if paid or declined:
            return paid, declined, amount, latest
    return False, '', 0, latest
