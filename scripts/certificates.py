"""Issue, validate, atomically activate and renew SAP Desk HTTPS certificates.

Host-side Docker orchestration: the Certbot container never receives Docker's socket.
Staging certificates live in a different directory and cannot be activated.
Requires Python 3, Docker Compose and OpenSSL >= 1.1.1 on the Debian server.
"""
import argparse
import fcntl
import hashlib
import ipaddress
import os
from pathlib import Path
import secrets
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from prepare_production_env import normalize_host, validate_email

ROOT = Path(__file__).resolve().parent.parent
CERT_NAME = 'sapdesk'
PUBLIC_KEYS = {'APP_DOMAIN', 'PUBLIC_BASE_URL', 'ACME_EMAIL', 'DJANGO_ALLOWED_HOSTS', 'CSRF_TRUSTED_ORIGINS', 'DJANGO_DEBUG'}


class CertError(Exception):
    pass


def load_config(path):
    values = {}
    for line in path.read_text().splitlines():
        key, separator, value = line.partition('=')
        if not separator or key.strip() not in PUBLIC_KEYS:
            continue
        parts = shlex.split(value, comments=True)
        if len(parts) > 1:
            raise CertError(f'{key.strip()} 的格式无效。')
        values[key.strip()] = parts[0] if parts else ''
    host = normalize_host(values.get('APP_DOMAIN', ''))
    if values.get('PUBLIC_BASE_URL', '').rstrip('/') != f'https://{host}':
        raise CertError('PUBLIC_BASE_URL 必须是 https:// 加 APP_DOMAIN 中的公网 IP 或域名。')
    if host not in values.get('DJANGO_ALLOWED_HOSTS', '').split(',') or f'https://{host}' not in values.get('CSRF_TRUSTED_ORIGINS', '').split(','):
        raise CertError('请将同一访问地址写入 ALLOWED_HOSTS 和 CSRF_TRUSTED_ORIGINS。')
    if values.get('DJANGO_DEBUG', '').lower() != 'false':
        raise CertError('公开部署必须设置 DJANGO_DEBUG=false。')
    values['APP_DOMAIN'] = host
    return values


def certificate_arguments(host):
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return ['-d', host]
    return ['--ip-address', host, '--required-profile', 'shortlived']


@contextmanager
def exclusive_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), 'a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CertError('另一个证书任务正在运行，请稍后再试。') from None
        yield


def atomic_link(link, target):
    temporary = link.with_name('.current-' + secrets.token_hex(8))
    try:
        temporary.symlink_to(target)
        os.replace(temporary, link)
    finally:
        temporary.unlink(missing_ok=True)


class Certificates:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.config = load_config(self.root / '.env.production')
        self.host = self.config['APP_DOMAIN']
        self.deploy = self.root / 'deploy'
        self.openssl = os.environ.get('OPENSSL_BIN', 'openssl')

    def run(self, arguments, capture=False, env=None, timeout=600):
        return subprocess.run(arguments, cwd=self.root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE if capture else None, check=True, env=env, timeout=timeout).stdout

    def compose(self, *arguments, certificates=False, staging=False, capture=False):
        config = 'compose.certificates.yaml' if certificates else 'compose.yaml'
        env = dict(os.environ)
        env.pop('COMPOSE_PROJECT_NAME', None)
        env['SAPDESK_ACME_CONFIG'] = str(self.deploy / ('acme-staging' if staging else 'letsencrypt'))
        return self.run(['docker', 'compose', '--env-file', str(self.root / '.env.production'), '-f', config, *arguments], capture=capture, env=env)

    def web_running(self):
        return bool(self.compose('ps', '--status', 'running', '--quiet', 'web', capture=True).strip())

    def prepare_directories(self):
        for name in ['letsencrypt', 'acme-staging', 'acme-work', 'acme-logs', 'certs']:
            (self.deploy / name).mkdir(parents=True, mode=0o700, exist_ok=True)
        # The Nginx worker must be able to traverse and read ACME challenges.
        for name in ['acme', 'acme/.well-known', 'acme/.well-known/acme-challenge']:
            path = self.deploy / name
            path.mkdir(mode=0o755, exist_ok=True)
            path.chmod(0o755)

    def probe_http(self):
        token = secrets.token_hex(16)
        probe = self.deploy / 'acme/.well-known/acme-challenge' / ('sapdesk-probe-' + token)
        probe.write_text(token)
        probe.chmod(0o644)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            for _ in range(10):
                try:
                    request = urllib.request.Request('http://127.0.0.1/.well-known/acme-challenge/' + probe.name, headers={'Host': self.host})
                    with opener.open(request, timeout=3) as response:
                        if response.read(100).decode() == token:
                            return
                except (OSError, ValueError):
                    pass
                time.sleep(0.5)
            raise CertError('本机 HTTP 验证目录不可读。请检查 80 端口归属和 Nginx 配置；不会停止其他服务。')
        finally:
            probe.unlink(missing_ok=True)

    def ssl_command(self, *arguments):
        try:
            return self.run([self.openssl, *map(str, arguments)], capture=True, timeout=20)
        except subprocess.CalledProcessError:
            raise CertError('OpenSSL 验证失败：' + str(arguments[0]) + '。检查证书信任链、有效期、访问 IP 和密钥是否匹配。') from None

    def validate(self, directory):
        directory = Path(directory)
        chain, key = directory / 'fullchain.pem', directory / 'privkey.pem'
        if not chain.is_file() or not key.is_file():
            raise CertError('证书文件尚未生成。')
        try:
            ipaddress.ip_address(self.host)
            check = '-verify_ip'
        except ValueError:
            check = '-verify_hostname'
        self.ssl_command('x509', '-in', chain, '-checkend', '86400', '-noout')
        # Both arguments can contain the full chain; the first certificate is the leaf.
        # System trust is required: a staging/self-signed certificate must never activate.
        # Older x509 -checkip/-checkhost can exit 0 on a mismatch; verify fails reliably.
        self.ssl_command('verify', '-purpose', 'sslserver', check, self.host, '-untrusted', chain, chain)
        certificate_key = self.ssl_command('x509', '-in', chain, '-pubkey', '-noout')
        private_key = self.ssl_command('pkey', '-in', key, '-pubout')
        if certificate_key.strip() != private_key.strip():
            raise CertError('证书和私钥不匹配。')
        return self.ssl_command('x509', '-in', chain, '-outform', 'DER')

    def live_certificate(self, expected):
        context = ssl.create_default_context()
        for _ in range(5):
            try:
                with socket.create_connection(('127.0.0.1', 443), timeout=5) as raw:
                    with context.wrap_socket(raw, server_hostname=self.host) as tls:
                        if tls.getpeercert(binary_form=True) == expected:
                            return
            except (OSError, ssl.SSLError):
                pass
            time.sleep(1)
        raise CertError('HTTPS 服务未加载预期证书，或证书无法通过 IP/信任校验。')

    def activate(self, running):
        source_root = self.deploy / 'letsencrypt'
        source = source_root / 'live' / CERT_NAME
        for name in ['fullchain.pem', 'privkey.pem']:
            if not (source / name).resolve().is_relative_to(source_root.resolve()):
                raise CertError('证书链接超出生产证书目录，拒绝安装。')
        expected = self.validate(source)
        chain, key = (source / 'fullchain.pem').read_bytes(), (source / 'privkey.pem').read_bytes()
        digest = hashlib.sha256(chain + key).hexdigest()
        certs = self.deploy / 'certs'
        releases = certs / 'releases'
        releases.mkdir(parents=True, mode=0o700, exist_ok=True)
        release = releases / digest
        if not release.exists():
            temporary = Path(tempfile.mkdtemp(prefix='.release-', dir=releases))
            try:
                for name, content, mode in [('fullchain.pem', chain, 0o644), ('privkey.pem', key, 0o600)]:
                    with os.fdopen(os.open(temporary / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode), 'wb') as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                temporary.rename(release)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        elif (release / 'fullchain.pem').read_bytes() != chain or (release / 'privkey.pem').read_bytes() != key:
            raise CertError('已有证书版本与预期不符，拒绝覆盖。')
        current = certs / 'current'
        if current.exists() and not current.is_symlink():
            raise CertError('certs/current 必须是由本脚本管理的链接，请保留现有文件后再处理。')
        previous = os.readlink(current) if current.is_symlink() else None
        atomic_link(current, 'releases/' + digest)
        try:
            if running:
                self.compose('exec', '-T', 'web', 'nginx', '-t')
                self.compose('exec', '-T', 'web', 'nginx', '-s', 'reload')
                self.live_certificate(expected)
        except Exception:
            if previous is None:
                current.unlink(missing_ok=True)
            else:
                atomic_link(current, previous)
                # Best-effort restore of the known previous certificate after failed reload.
                try:
                    self.compose('exec', '-T', 'web', 'nginx', '-t')
                    self.compose('exec', '-T', 'web', 'nginx', '-s', 'reload')
                except Exception:
                    print('旧证书链接已恢复，但 Nginx 重载仍失败，请检查服务。', file=sys.stderr)
            raise
        print('证书已验证并激活，HTTPS 实际证书一致。' if running else '可信证书已准备；启动正式 web 服务后运行 check --live。')

    def issue(self, staging, agree_tos):
        if not agree_tos:
            raise CertError('首次签发需阅读 ACME 协议后显式添加 --agree-tos。')
        email = validate_email(self.config.get('ACME_EMAIL', ''))
        self.prepare_directories()
        running = self.web_running()
        try:
            if not running:
                self.compose('up', '-d', 'challenge', certificates=True)
            self.probe_http()
            arguments = ['run', '--rm', '-T', '--no-deps', 'certbot', 'certonly', '--non-interactive', '--agree-tos', '--email', email, '--cert-name', CERT_NAME, '--webroot', '--webroot-path', '/var/www/acme', '--keep-until-expiring', *certificate_arguments(self.host)]
            if staging:
                arguments.append('--staging')
            self.compose(*arguments, certificates=True, staging=staging)
            if staging:
                print('预演签发完成；测试证书已隔离，未安装到正式站点。')
            else:
                self.activate(running)
        finally:
            if not running:
                try:
                    self.compose('stop', 'challenge', certificates=True)
                except Exception:
                    print('临时验证服务未能停止，请执行证书 compose 的 stop challenge。', file=sys.stderr)

    def renew(self, dry_run):
        self.prepare_directories()
        if not self.web_running():
            raise CertError('正式 web 服务未运行，续期验证目录不可用；请先恢复服务。')
        self.probe_http()
        arguments = ['run', '--rm', '-T', '--no-deps', 'certbot', 'renew', '--non-interactive', '--cert-name', CERT_NAME]
        if dry_run:
            arguments.append('--dry-run')
        self.compose(*arguments, certificates=True)
        if dry_run:
            print('续期预演完成；未替换线上证书。')
        else:
            # Also repairs a previously issued certificate whose activation failed.
            self.activate(True)

    def check(self, live):
        directory = self.deploy / 'certs/current'
        expected = self.validate(directory)
        if live:
            self.live_certificate(expected)
        print('证书检查通过：' + self.host)
        print(self.ssl_command('x509', '-in', directory / 'fullchain.pem', '-enddate', '-noout').decode().strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    issue = sub.add_parser('issue')
    issue.add_argument('--staging', action='store_true')
    issue.add_argument('--agree-tos', action='store_true')
    renew = sub.add_parser('renew')
    renew.add_argument('--dry-run', action='store_true')
    check = sub.add_parser('check')
    check.add_argument('--live', action='store_true')
    args = parser.parse_args()
    try:
        with exclusive_lock(ROOT / 'deploy/.acme.lock'):
            manager = Certificates()
            if args.command == 'issue':
                manager.issue(args.staging, args.agree_tos)
            elif args.command == 'renew':
                manager.renew(args.dry_run)
            else:
                manager.check(args.live)
    except (CertError, ValueError, OSError, subprocess.SubprocessError) as exc:
        # Do not serialize environment variables or certificate/private-key contents.
        print('证书任务失败：' + (str(exc) if isinstance(exc, (CertError, ValueError)) else type(exc).__name__) + '。请检查上方命令输出。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
