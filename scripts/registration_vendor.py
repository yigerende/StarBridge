"""Import tracked source without runtime data, credentials or dependencies."""
import json
from pathlib import Path
import shutil
import subprocess
from registration_verify import source_hash

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / 'gpt-register'
DESTINATION = ROOT / 'services/gpt-register'
MANIFEST = ROOT / 'docs/registration-source-manifest.json'


def main():
    if DESTINATION.exists():
        raise SystemExit('Destination already exists; refusing to overwrite source')
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=SOURCE, timeout=20).decode().split('\0')
    files = {}
    for name in filter(None, names):
        if name.lower().endswith('.md') or name.startswith('.github/'):
            continue
        source = SOURCE / name
        if not source.is_file() or source.is_symlink():
            raise SystemExit(f'Unsupported tracked entry: {name}')
        target = DESTINATION / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        files[name] = source_hash(source.read_bytes())
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, timeout=20).decode().strip()
    MANIFEST.write_text(json.dumps({'repository': 'yigerende/gptreg', 'revision': revision,'hashing':'sha256; text normalized to LF, binary exact',
                                  'files': files, 'integration_changes': []}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'revision': revision, 'files': len(files)}))


if __name__ == '__main__':
    main()
