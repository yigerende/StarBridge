"""Read the original transaction after submission; never replay a charge here."""
import logging
import re
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
        return _object(getattr(exc, 'payment_response', None))


def remember(context, data):
    """Keep only original-transaction references, never card data or raw bodies."""
    data = _object(data)
    identity = data.get('id')
    if isinstance(identity, str) and identity.startswith('cs_') and identity != context['checkout_id']:
        return False
    updated = dict(context)
    if data.get('currency') and context.get('currency') and str(data['currency']).upper() != context['currency']:
        return False
    eid = data.get('eid')
    if isinstance(eid, str) and 0 < len(eid) <= 256:
        updated['eid'] = eid
    for node in (data, _object(data.get('submission_attempt')), _object(data.get('error'))):
        intent = _object(node.get('payment_intent'))
        identity = intent.get('id')
        if identity:
            if not isinstance(identity, str) or not re.fullmatch(r'pi_[A-Za-z0-9]+', identity):
                return False
            if updated.get('intent_id') and updated['intent_id'] != identity:
                return False
            updated['intent_id'] = identity
        if intent.get('currency') and context.get('currency') and str(intent['currency']).upper() != context['currency']:
            return False
        secret = intent.get('client_secret')
        if not isinstance(secret, str) or not re.fullmatch(r'pi_[A-Za-z0-9]+_secret_[A-Za-z0-9]+', secret):
            continue
        if identity != secret.split('_secret_')[0]:
            return False
        updated['client_secret'] = secret
    context.update(updated)
    return True


def verification_context(checkout_id, public_key, *responses, currency=''):
    context = {'checkout_id': checkout_id, 'public_key': public_key, 'eid': 'NA', 'currency': currency}
    for data in responses:
        if not remember(context, data):
            raise ValueError('checkout_checkpoint_mismatch')
    return context


def _read(session, method, url, context, **kwargs):
    # Record the provider's bounded/redacted business error, not only HTTP 400.
    with trace.request('payment_read') as observation:
        response = getattr(session, method)(url, timeout=30, **kwargs)
        observation.response(response.status_code, response.headers, response.text,
                             failure_details=True, secrets=tuple(context.values()))
        try:
            data = _object(response.json())
        except (ValueError, TypeError):
            data = {}
        return response.status_code, data


def _original_result(stripe, checkout_id, public_key, context):
    status, data = _read(stripe.session, 'get',
        f'https://api.stripe.com/v1/payment_pages/{checkout_id}', context,
        params={'key': public_key, 'eid': context.get('eid', 'NA')})
    if not remember(context, data):
        return None
    if status == 200 and any(classify(data)[:2]):
        return data
    # The hosted page can close after payment. Query the original intent when
    # its client secret was captured before/during submission.
    if context.get('intent_id') and context.get('client_secret'):
        code, intent = _read(stripe.session, 'get',
            'https://api.stripe.com/v1/payment_intents/' + context['intent_id'], context,
            params={'key': public_key, 'client_secret': context['client_secret']})
        if code == 200 and intent.get('id') == context['intent_id']:
            result = {'payment_intent': intent}
            if remember(context, result) and any(classify(result)[:2]):
                return result
    if _object(data.get('error')).get('code') == 'checkout_not_active_session':
        # Initialization reads the SAME checkout's display/state. It carries no
        # card/payment method and cannot confirm a charge or create a checkout.
        code, initialized = _read(stripe.session, 'post',
            f'https://api.stripe.com/v1/payment_pages/{checkout_id}/init', context,
            data={'key': public_key, 'eid': context.get('eid', 'NA'),
                  'browser_locale': 'en-US', 'browser_timezone': 'UTC', 'redirect_type': 'url'})
        if code == 200 and remember(context, initialized):
            return initialized
    return data if status == 200 else None


def poll_result(stripe, checkout_id, public_key, *, context=None, attempts=5, save=None):
    """Bounded reads on the same checkout, HTTP session and order proxy."""
    latest = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(1.5)
        try:
            if context is None:
                response = trace.http_call('payment_read', stripe.session.get,
                    f'https://api.stripe.com/v1/payment_pages/{checkout_id}',
                    params={'key': public_key, 'eid': 'NA'}, timeout=30)
                if response.status_code != 200:
                    continue
                latest = response.json()
            else:
                latest = _original_result(stripe, checkout_id, public_key, context)
                if save:
                    save(context)
        except Exception as exc:
            # Even a runtime/JSON/network error on a read must not skip later reads.
            log.warning('Payment result read unavailable: %s', trace.error_code(exc))
            continue
        paid, declined, amount = classify(latest)
        if paid or declined:
            return paid, declined, amount, latest
    return False, '', 0, latest
