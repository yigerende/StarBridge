"""Bounded private checkout IPC; credential frames never become result/log data."""
import json
import os
from queue import Queue, Empty, Full
import subprocess
import threading
import time

from . import checkout_diagnostics
from .checkout_errors import CheckoutError

LINE_LIMIT = 1024 * 1024
DIAGNOSTIC_LIMIT = 256 * 1024


def run(args, session_json, *, cwd, environment, timeout, save):
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    process = subprocess.Popen([*args, '--credential-checkpoints'], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd,
                               creationflags=flags, env=environment)
    messages = Queue(maxsize=8)
    stopped = threading.Event()
    diagnostic = bytearray()
    deadline = time.monotonic() + timeout

    def put(item):
        while not stopped.is_set():
            try:
                messages.put(item, timeout=0.1)
                return
            except Full:
                pass

    def read_output():
        try:
            while not stopped.is_set():
                raw = process.stdout.readline(LINE_LIMIT + 1)
                if not raw or len(raw) > LINE_LIMIT:
                    put(None)
                    return
                put(raw)
        except Exception:
            put(None)

    def read_diagnostics():
        try:
            while True:
                chunk = process.stderr.read(8192)
                if not chunk:
                    return
                diagnostic.extend(chunk[:max(0, DIAGNOSTIC_LIMIT-len(diagnostic))])
        except Exception:
            pass

    readers = [threading.Thread(target=read_output, daemon=True), threading.Thread(target=read_diagnostics, daemon=True)]
    for reader in readers:
        reader.start()
    try:
        process.stdin.write(session_json.encode('utf-8') + b'\n')
        process.stdin.flush()
        for _ in range(32):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CheckoutError('timeout')
            try:
                raw = messages.get(timeout=remaining)
            except Empty:
                raise CheckoutError('timeout') from None
            try:
                value = json.loads(raw) if raw else None
            except (ValueError, UnicodeError):
                raise CheckoutError('invalid_response') from None
            if not isinstance(value, dict):
                raise CheckoutError('invalid_response')
            if value.get('type') == 'credentials':
                try:
                    save(value.get('credentials'))
                except Exception:
                    raise CheckoutError('checkpoint_failed') from None
                process.stdin.write(b'{"ok":true}\n')
                process.stdin.flush()
            elif 'type' not in value and ('checkout_id' in value or 'error_code' in value):
                process.stdin.close()
                code = process.wait(timeout=max(0.01, deadline-time.monotonic()))
                return subprocess.CompletedProcess(args, code, raw, b'')
            else:
                raise CheckoutError('invalid_response')
        raise CheckoutError('invalid_response')
    except subprocess.TimeoutExpired:
        raise CheckoutError('timeout') from None
    except OSError:
        raise CheckoutError('invalid_response') from None
    finally:
        stopped.set()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        for reader in readers:
            reader.join(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
        checkout_diagnostics.submit(bytes(diagnostic))
