"""Verify imported source and the explicitly reviewed integration changes."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def source_hash(data):
    return hashlib.sha256(data if b'\0' in data else data.replace(b'\r\n',b'\n')).hexdigest()


def main():
    manifest=json.loads((ROOT/'docs/registration-source-manifest.json').read_text(encoding='utf-8'))
    changes={row['path']:row['sha256'] for row in manifest['integration_changes']}
    unchanged=0
    for name,original in manifest['files'].items():
        protected=(name.startswith('src/') and not name.startswith('src/platform/')) or name.startswith('bin/') or name in ('index.js','web/src/App.tsx')
        if protected and name in changes:raise SystemExit('Engine changes are forbidden: '+name)
        file=ROOT/'services/gpt-register'/name
        if not file.is_file():raise SystemExit('Missing imported source: '+name)
        actual=source_hash(file.read_bytes())
        if actual!=changes.get(name,original):raise SystemExit('Unexpected source change: '+name)
        unchanged+=actual==original
    if set(changes)-set(manifest['files']):raise SystemExit('Integration list includes untracked source names')
    raw=subprocess.check_output(['node','-e',"process.stdout.write(JSON.stringify(require('./services/gpt-register/src/platform/openapi').openapi))"],cwd=ROOT,timeout=15)
    if json.loads(raw)!=json.loads((ROOT/'backend/registration-openapi.json').read_text(encoding='utf-8')):
        raise SystemExit('Run node scripts/registration_openapi.cjs to update the registration API contract')
    print(json.dumps({'source_revision':manifest['revision'],'imported_files':len(manifest['files']),
                      'unchanged_files':unchanged,'integration_changes':len(changes),'engine_unchanged':True}))


if __name__=='__main__':main()
