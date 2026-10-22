"""Build the portable pilot from an explicit, credential-free allowlist."""
from pathlib import Path
import hashlib
import zipfile

ROOT = Path(__file__).resolve().parent.parent
FILES = ['connector.mjs', '启动连接器.command', 'README.md']
WINDOWS_FILES = ['Start-Connector.cmd', 'Start-Connector.ps1', 'Connector.Core.ps1',
                 'Read-Ds4Gui.ps1', 'Check-DS4.cmd', 'Check-DS4.ps1', 'README.md']


def package(platform='mac'):
    if platform not in ['mac', 'windows']:
        raise ValueError('Unsupported package platform')
    files = FILES if platform == 'mac' else WINDOWS_FILES
    folder = ROOT / 'connector' if platform == 'mac' else ROOT / 'connector/windows'
    title = 'SAPDesk-Connector-Mac-Pilot' if platform == 'mac' else 'SAPDesk-Connector-Windows-Pilot'
    destination = ROOT / f'release/{title}.zip'
    destination.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            source = folder / name
            if source.is_symlink():
                raise ValueError('Connector package sources must be regular files')
            info = zipfile.ZipInfo.from_file(source, title + '/' + name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = ((0o100755 if name.endswith('.command') else 0o100644) << 16)
            content = source.read_bytes()
            if platform == 'windows':
                content = source.read_text().replace('\r\n', '\n').replace('\n', '\r\n').encode('utf-8')
                if name == 'README.md':
                    content = b'\xef\xbb\xbf' + content
            archive.writestr(info, content)
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(files)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix('.zip.sha256').write_text(digest + '  ' + destination.name + '\n')
    print(f'{destination.name}: {destination.stat().st_size} bytes; source only, no SAP credentials/runtime/data')


if __name__ == '__main__':
    package()
    package('windows')
