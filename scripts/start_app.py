"""Run both backend modules as one application, with coordinated shutdown."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    from backend import db
    from backend.seed import seed
    db.initialize();seed();db.close()
    children=[]
    stopping=False
    def stop(*_):
        nonlocal stopping
        stopping=True
        for process in reversed(children):
            if process.poll() is None:process.terminate()
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    try:
        if os.getenv('XUFU_INTEGRATION')=='1':
            env={**os.environ,'PORT':'3210','LISTEN_HOST':'127.0.0.1'}
            node=subprocess.Popen(['node','docker/server.js'],cwd=ROOT/'registration',env=env)
            children.append(node)
            for _ in range(120):
                if stopping or node.poll() is not None:raise RuntimeError('Registration adapter did not start')
                try:
                    with urllib.request.urlopen('http://127.0.0.1:3210/healthz',timeout=1) as r:
                        if r.status==200:break
                except OSError:time.sleep(.25)
            else:raise RuntimeError('Registration adapter health timed out')
        if not stopping:children.append(subprocess.Popen([sys.executable,'scripts/serve.py'],cwd=ROOT))
        while not stopping and all(p.poll() is None for p in children):time.sleep(.25)
        if not stopping:raise RuntimeError('An application module exited')
    finally:
        stop()
        deadline=time.monotonic()+125
        for process in reversed(children):
            try:process.wait(timeout=max(1,deadline-time.monotonic()))
            except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__=='__main__':main()
