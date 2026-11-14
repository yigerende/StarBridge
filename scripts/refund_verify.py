"""Verify the imported HUIXU protocol and the reviewed StarBridge adapters."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 2026-11-12 explicit repair authorization; all other imported protocol/worker
# files retain their original hash. See docs/refund-resource-recovery.md.
RESOURCE_RECOVERY_ADAPTATIONS = {
    'runtime/refund_runner.py', 'runtime/manager_oauth/adapter.py',
    # 2026-11-14: user-authorized fallback wait and private matching evidence.
    'runtime/refund_plan_fallback.py', 'runtime/test_refund_plan_fallback.py',
    'runtime/diagnostics.py', 'runtime/resource_guard.py',
    'runtime/test_member_completion.py', 'runtime/test_password_failure.py',
    'runtime/test_refund_transport.py', 'runtime/test_resource_guard.py',
}


def source_hash(data):
    return hashlib.sha256(data.replace(b'\r\n', b'\n')).hexdigest()


def main():
    manifest = json.loads((ROOT / 'docs/refund-source-manifest.json').read_text(encoding='utf-8'))
    paths = set()
    unchanged = runtime = 0
    for entry in manifest['files']:
        name = entry['path']
        if name in paths:
            raise SystemExit('Duplicate manifest entry: ' + name)
        paths.add(name)
        file = ROOT / 'services/refund-core' / name
        if not file.is_file() or source_hash(file.read_bytes()) != entry['sha256']:
            raise SystemExit('Unexpected source change: ' + name)
        same = entry['sha256'] == entry['source_sha256']
        if name.startswith(('runtime/', 'internal/refundworker/')) and not same and name not in RESOURCE_RECOVERY_ADAPTATIONS:
            raise SystemExit('Original protocol and worker must remain unchanged: ' + name)
        if not same and not entry.get('adaptation'):
            raise SystemExit('Missing adapter explanation: ' + name)
        unchanged += same
        runtime += name.startswith('runtime/')
    print(json.dumps({'source_revision': manifest['revision'], 'imported_files': len(paths),
                      'unchanged_files': unchanged, 'adapted_files': len(paths) - unchanged,
                      'runtime_files': runtime,
                      'authorized_protocol_adaptations': [e['path'] for e in manifest['files'] if e['path'] in RESOURCE_RECOVERY_ADAPTATIONS and e['sha256'] != e['source_sha256']],
                      'worker_unchanged': all(e['sha256'] == e['source_sha256'] for e in manifest['files'] if e['path'].startswith('internal/refundworker/'))}))


if __name__ == '__main__':
    main()
