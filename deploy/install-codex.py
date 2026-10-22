"""Install the pinned official native Linux CLI, verifying npm's SHA-512 integrity."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import tarfile
import urllib.parse
import urllib.request

VERSION = '0.158.0'


def main():
    arch = {'x86_64': ('x64', 'x86_64-unknown-linux-musl'),
            'aarch64': ('arm64', 'aarch64-unknown-linux-musl')}[platform.machine()]
    package_version = f'{VERSION}-linux-{arch[0]}'
    with urllib.request.urlopen(f'https://registry.npmjs.org/@openai%2Fcodex/{package_version}', timeout=60) as response:
        meta = json.load(response)
    if meta['version'] != package_version:
        raise RuntimeError('Codex version mismatch')
    url = meta['dist']['tarball']
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname != 'registry.npmjs.org':
        raise RuntimeError('Unexpected package origin')
    with urllib.request.urlopen(url, timeout=180) as response:
        data = response.read(512 * 1024 * 1024 + 1)
    if len(data) > 512 * 1024 * 1024:
        raise RuntimeError('Codex package exceeds size limit')
    actual = 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode()
    if actual != meta['dist']['integrity']:
        raise RuntimeError('Codex package integrity mismatch')
    prefix = f'package/vendor/{arch[1]}/'
    root = Path('/opt/codex')
    installed = []
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for member in archive.getmembers():
            if not member.name.startswith(prefix) or member.isdir():
                continue
            relative = Path(member.name[len(prefix):])
            if relative.is_absolute() or '..' in relative.parts or not member.isfile() or member.size > 512 * 1024 * 1024:
                raise RuntimeError('Unexpected package member')
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as stream, path.open('wb') as output:
                while chunk := stream.read(1024 * 1024):
                    output.write(chunk)
            os.chmod(path, 0o755 if member.mode & 0o111 else 0o644)
            installed.append(str(relative))
    if not (root / 'bin/codex').is_file() or not (root / 'codex-path/rg').is_file():
        raise RuntimeError('Native executables missing')
    Path('/usr/local/bin/codex').symlink_to(root / 'bin/codex')
    Path('/usr/local/bin/rg').symlink_to(root / 'codex-path/rg')
    print('Verified Codex', VERSION, 'installed with', len(installed), 'runtime files')


if __name__ == '__main__':
    main()
