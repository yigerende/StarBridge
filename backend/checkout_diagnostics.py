"""Bounded, asynchronous, private checkout evidence; never part of order decisions."""
import hashlib
import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import re
import threading
import time

from .checkout_errors import safe_code

MAX_BYTES = 256 * 1024
MAX_EVENTS = 192
_VERSION = re.compile(r'[0-9a-f]{24}')
_ORDER = re.compile(r'[a-zA-Z0-9_-]{1,64}')
_EXTRA_CODES = frozenset('RefreshAccessTokenError RefreshTokenError OAuthCallbackError OAuthSessionExpired '
    'SessionExpired invalid_grant invalid_token invalid_request invalid_request_error authentication_error '
    'token_expired session_revoked account_deactivated account_disabled access_denied other'.split())
_ENUMS = {
    'event': {'input_credentials', 'session_refresh', 'http_request', 'http_retry_wait', 'phase', 'transaction', 'price', 'checkpoint'},
    'step': {'homepage', 'session_refresh', 'account_check', 'subscription_check', 'checkout_create', 'verification', 'checkout_price', 'payment_bootstrap', 'other'},
    'method': {'GET', 'POST', 'PATCH', 'DELETE', 'PUT'}, 'service': {'chatgpt', 'stripe'},
    'outcome': {'started', 'completed', 'succeeded', 'failed', 'retry', 'retrying', 'received', 'saved', 'skipped'},
    'phase': {'initializing', 'account_checked', 'stripe_ready', 'checkout_created', 'checkout_ready'},
}
_BOOLS = {'has_at', 'has_session', 'response_has_at', 'response_has_error', 'response_has_account', 'account_matches',
          'at_version_changed', 'session_version_changed', 'auth_cookie_version_changed'}
_VERSIONS = {'at_version', 'session_version', 'auth_cookie_version', 'before_at_version', 'before_session_version', 'before_auth_cookie_version'}
_NUMBERS = {'elapsed_ms', 'attempt', 'status', 'durationMs', 'remainingDeadlineMs', 'connectionReadyMs', 'requestWriteMs',
            'firstByteMs', 'responseWaitMs', 'retryWaitElapsedMs'}
_CODES = {'error_code', 'response_error', 'errorCode', 'responseErrorCode', 'responseErrorType'}


def diagnostic_key(order_id):
    """Same order can compare versions; different orders cannot correlate tokens."""
    key = os.environ.get('MASTER_KEY', '').encode()
    if not key or not isinstance(order_id, str) or not _ORDER.fullmatch(order_id):
        return ''
    return hmac.new(key, ('checkout-diagnostics:' + order_id).encode(), hashlib.sha256).hexdigest()


def _event(value):
    if not isinstance(value, dict) or not isinstance(value.get('event'), str) or value['event'] not in _ENUMS['event']:
        return None
    result = {}
    for key, item in value.items():
        if key in _ENUMS and isinstance(item, str) and item in _ENUMS[key]:
            result[key] = item
        elif key in _BOOLS and type(item) is bool:
            result[key] = item
        elif key in _VERSIONS and isinstance(item, str) and _VERSION.fullmatch(item):
            result[key] = item
        elif key in _NUMBERS and type(item) in (int, float) and 0 <= item <= 3600000:
            result[key] = round(item, 2)
        elif key in _CODES and isinstance(item, str):
            result[key] = item if item in _EXTRA_CODES or item == '' else safe_code(item)
    return result


def decode(raw):
    """Treat subprocess output as untrusted. No text, URLs or headers are retained."""
    for line in reversed(raw[-MAX_BYTES:].splitlines()):
        if not line.startswith(b'{') or b'"starbridge_checkout_diagnostics"' not in line:
            continue
        try:
            frame = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(frame, dict) or frame.get('starbridge_checkout_diagnostics') != 1 or not isinstance(frame.get('events'), list):
            continue
        events = [filtered for item in frame['events'][:MAX_EVENTS] if (filtered := _event(item))]
        dropped = frame.get('dropped', 0)
        return {'events': events, 'collector_dropped': dropped if type(dropped) is int and 0 <= dropped <= 1000000 else 0}
    return None


class _PrivateFile(RotatingFileHandler):
    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        return os.fdopen(fd, 'a', encoding='utf-8')


class AsyncDiagnostics:
    def __init__(self, directory=None, capacity=64, sink=None):
        self.directory = directory
        self.queue = queue.Queue(maxsize=capacity)
        self.sink = sink
        self.stop_event = threading.Event()
        self.thread = None
        self.dropped = 0
        self.write_failures = 0
        self.written = 0

    def start(self):
        if self.thread is not None:
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name='checkout-diagnostics')
        self.thread.start()

    def submit(self, order_id, round_number, raw):
        # No JSON parsing, filesystem access, logging or database work here.
        if not self.thread or self.stop_event.is_set() or not isinstance(raw, bytes) or not raw:
            return
        if not isinstance(order_id, str) or not _ORDER.fullmatch(order_id):
            return
        try:
            self.queue.put_nowait((order_id, round_number if type(round_number) is int else None, time.time(), raw[-MAX_BYTES:]))
        except queue.Full:
            self.dropped += 1

    def _run(self):
        handler = None
        try:
            while not self.stop_event.is_set() or not self.queue.empty():
                try:
                    order_id, round_number, received_at, raw = self.queue.get(timeout=.2)
                except queue.Empty:
                    continue
                try:
                    data = decode(raw)
                    if data is None:
                        continue
                    data.update(order_id=order_id, round=round_number, received_at=received_at,
                                queue_dropped=self.dropped, write_failures=self.write_failures)
                    if self.sink is not None:
                        self.sink(data)
                    else:
                        if handler is None:
                            directory = Path(self.directory or os.getenv('MAINTENANCE_DIR', '.maintenance')) / 'diagnostics'
                            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                            handler = _PrivateFile(directory / 'checkout.jsonl', maxBytes=10*1024*1024, backupCount=4, encoding='utf-8')
                        message = json.dumps(data, ensure_ascii=True, separators=(',', ':'))
                        # Call doRollover/write directly: standard emit swallows errors
                        # and could print the rejected log record via handleError.
                        if handler.stream.tell() + len(message) + 1 >= handler.maxBytes:
                            handler.doRollover()
                        handler.stream.write(message + '\n')
                        handler.stream.flush()
                    self.written += 1
                except Exception:
                    self.write_failures += 1
                    if handler is not None:
                        try: handler.close()
                        except Exception: pass
                        handler = None
                    # No raw exceptions or payloads, and no caller-thread fallback.
                    if self.write_failures == 1 or self.write_failures % 100 == 0:
                        logging.getLogger('starbridge.checkout_diagnostics').warning(
                            'Private checkout diagnostic write failed; failures=%d', self.write_failures)
                finally:
                    self.queue.task_done()
        finally:
            if handler is not None:
                try: handler.close()
                except Exception: pass

    def stop(self, timeout=2):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout)
            if not self.thread.is_alive():
                self.thread = None


writer = AsyncDiagnostics()


def submit(raw):
    # Capture identity before leaving the ContextVar's order scope.
    from .payment_trace import diagnostic_identity
    try:
        identity = diagnostic_identity()
        if identity:
            writer.submit(identity[0], identity[1], raw)
    except Exception:
        # Diagnostic failures must never enter checkout/payment error handling.
        pass
