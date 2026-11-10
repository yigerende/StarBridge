"""Build the private execution module without starting any business queue."""
import os
from pathlib import Path
import subprocess

root=Path(__file__).resolve().parents[1]
(root/'bin').mkdir(exist_ok=True)
binary=root/'bin'/('refund-core.exe' if os.name=='nt' else 'refund-core')
subprocess.run(['go','build','-trimpath','-o',str(binary),'./cmd/bridge'],cwd=root/'services/refund-core',check=True)
print('Refund execution module built')
