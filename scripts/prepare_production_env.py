"""Generate production config for a public IPv4 address or domain; never overwrite secrets."""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import secrets


def normalize_host(value):
    value = value.strip()
    if any(c in value for c in '/:@?#\\\n\r\t ') or not value:
        raise ValueError('请输入纯公网 IPv4 地址或域名，不含协议、端口、路径或空格。')
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        if re.fullmatch(r'[0-9.]+', value):
            raise ValueError('IPv4 地址格式无效。') from None
        try:
            domain = value.encode('idna').decode('ascii').lower()
        except UnicodeError:
            raise ValueError('域名格式无效。') from None
        labels = domain.split('.')
        if len(domain) > 253 or len(labels) < 2 or labels[-1].isdigit() or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels):
            raise ValueError('域名格式无效。')
        return domain
    if ip.version != 4 or not ip.is_global or ip.is_multicast:
        raise ValueError('自动签发仅支持公网 IPv4；不能使用内网、回环或保留地址。')
    return str(ip)


def validate_email(email):
    if not re.fullmatch(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}', email):
        raise ValueError('请填写有效的 ACME 账户联系邮箱。')
    return email


def render_env(template, host, email=''):
    host = normalize_host(host)
    if email:
        validate_email(email)
    contents = template.replace('desk.example.com', host)
    for key in ['DJANGO_SECRET_KEY', 'POSTGRES_PASSWORD', 'POSTGRES_ADMIN_PASSWORD']:
        contents = contents.replace(key + '=\n', key + '=' + secrets.token_urlsafe(48) + '\n')
    return contents.replace('ACME_EMAIL=\n', 'ACME_EMAIL=' + email + '\n')


def write_new(path, contents):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(contents)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('host', help='公网 IPv4 或域名，例如 43.130.36.105')
    parser.add_argument('--email', default='', help='ACME 联系邮箱，也可稍后在 .env.production 填写')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    try:
        contents = render_env((root / '.env.example').read_text(), args.host, args.email)
        write_new(root / '.env.production', contents)
    except (ValueError, FileExistsError) as exc:
        parser.error('配置已存在，不覆盖密钥。需要改地址时请编辑原配置。' if isinstance(exc, FileExistsError) else str(exc))
    print('已创建 .env.production（0600），HTTPS 地址已设置；密钥未输出，钉钉保持关闭。')


if __name__ == '__main__':
    main()
