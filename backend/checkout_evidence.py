"""Bounded private per-order index, written ONLY by the diagnostic worker.

No database writes or business hooks. Admin reads never scan the rotating log.
"""
import json
import os
from pathlib import Path
from .checkout_diagnostics import MAX_BYTES, _ORDER, _event

MAX_ORDERS = 512
MAX_ROUNDS = 4


def read(order_id, directory=None):
    if not isinstance(order_id, str) or not _ORDER.fullmatch(order_id):
        return []
    directory = Path(directory or os.getenv('MAINTENANCE_DIR', '.maintenance')) / 'diagnostics' / 'orders'
    path = directory / (order_id + '.json')
    try:
        if path.is_symlink() or directory.is_symlink():
            return []
        with path.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return []
        rows = json.loads(raw)
        result = []
        for row in rows[-MAX_ROUNDS:]:
            if not isinstance(row, dict) or type(row.get('round')) is not int:
                continue
            events = row.get('events')
            if not isinstance(events, list):
                continue
            result.append({'round': row['round'], 'events': [e for v in events[:80] if (e := _event(v))]})
        return result
    except (OSError, ValueError, TypeError, AttributeError):
        return []


def save(diagnostic_directory, data):
    order_id, number = data['order_id'], data.get('round')
    if not _ORDER.fullmatch(order_id) or type(number) is not int:
        return
    directory = Path(diagnostic_directory) / 'orders'
    if directory.is_symlink():
        raise OSError('Invalid diagnostic directory')
    rows = read(order_id, Path(diagnostic_directory).parent)
    events = data['events']
    if not rows and not any((e.get('outcome') == 'failed' or e.get('status', 0) >= 400)
                            and not (e.get('step') == 'subscription_check' and e.get('status') == 404) for e in events):
        return
    directory.mkdir(mode=0o700, exist_ok=True)
    # Keep the beginning and end; first rejection and final failure stay visible.
    events = events if len(events) <= 80 else events[:40] + events[-40:]
    rows = [r for r in rows if r['round'] != number] + [{'round': number, 'events': events}]
    rows = sorted(rows, key=lambda r: r['round'])
    if len(rows) > MAX_ROUNDS:
        rows = rows[:1] + rows[-(MAX_ROUNDS-1):]
    payload = json.dumps(rows, ensure_ascii=True, separators=(',', ':')).encode()
    while len(payload) > MAX_BYTES and len(rows) > 1:
        rows.pop(1)
        payload = json.dumps(rows, ensure_ascii=True, separators=(',', ':')).encode()
    if len(payload) > MAX_BYTES:
        return
    target = directory / (order_id + '.json')
    temporary = target.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(payload)
    os.replace(temporary, target)
    # Only this one background writer manages these bounded summaries.
    files = sorted(directory.glob('*.json'), key=lambda p: p.stat().st_mtime)
    for path in files[:-MAX_ORDERS]:
        path.unlink(missing_ok=True)
