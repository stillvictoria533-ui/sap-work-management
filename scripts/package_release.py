"""Build a source-only deployment bundle. Excludes runtime data, credentials and certificates."""
import hashlib
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parent.parent
TOP_LEVEL = ['README.md', '.env.example', '.dockerignore', '.gitignore', 'Dockerfile', 'compose.yaml', 'compose.certificates.yaml']
FRONTEND_FILES = ['package.json', 'package-lock.json', 'index.html', 'vite.config.ts', 'tsconfig.json']


def package_files(root):
    root = Path(root)
    candidates = [root / name for name in TOP_LEVEL]
    candidates += list((root / 'backend').rglob('*.py'))
    candidates += [root / 'backend/requirements.txt']
    candidates += [root / 'frontend' / name for name in FRONTEND_FILES]
    candidates += list((root / 'frontend/src').rglob('*'))
    candidates += list((root / 'frontend/tests').glob('*.test.ts'))
    candidates += list((root / 'frontend/public').rglob('*'))
    candidates += [p for p in (root / 'deploy').glob('*') if p.suffix in ['.template', '.service', '.timer', '.sh', '.sql', '.py']]
    candidates += [p for p in (root / 'scripts').glob('*') if p.suffix in ['.py', '.sh']]
    candidates += list((root / 'docs').rglob('*.md'))
    candidates += list((root / 'tests').glob('test_*.py'))
    forbidden_parts = {'node_modules', '__pycache__', '.venv', 'private-media', 'certs', 'letsencrypt', 'acme-staging', 'backups'}
    files = []
    for path in candidates:
        relative = path.relative_to(root)
        if not path.is_file() or path.is_symlink() or forbidden_parts.intersection(relative.parts):
            continue
        if path.suffix in ['.pem', '.key', '.sqlite3', '.pyc'] or (path.name.startswith('.env') and path.name != '.env.example'):
            continue
        files.append(path)
    return sorted(set(files))


def create_package(root, output):
    root, output = Path(root), Path(output)
    files = package_files(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    def ownership(info):
        info.uid = info.gid = 0
        info.uname = info.gname = 'root'
        info.mode = 0o644
        return info
    with tarfile.open(output, 'w:gz') as archive:
        for path in files:
            archive.add(path, arcname='sapdesk/' + path.relative_to(root).as_posix(), recursive=False, filter=ownership)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + '.sha256').write_text(digest + '  ' + output.name + '\n')
    output.with_suffix(output.suffix + '.manifest.txt').write_text('\n'.join(p.relative_to(root).as_posix() for p in files) + '\n')
    return len(files), output.stat().st_size


if __name__ == '__main__':
    destination = ROOT / 'release/sap-desk-ip-deploy.tar.gz'
    count, size = create_package(ROOT, destination)
    print(f'Created {destination.name}: {count} files, {size} bytes. No runtime credentials/data included.')
