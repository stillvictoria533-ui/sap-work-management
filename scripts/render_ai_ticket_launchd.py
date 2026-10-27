"""Render a reviewable macOS LaunchAgent plist; does not install or start it."""
import argparse
import os
from pathlib import Path
import plistlib
import re
import shutil
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / 'deploy/com.sapdesk.ai-ticket-dispatcher.plist.template'


def render(mode, sap_workspace=None, ps4_user=None):
    python = shutil.which('python3')
    node = shutil.which('node')
    codex = Path.home() / '.codex/runtime/codex-cli-0.162.1/node_modules/.bin/codex'
    if not python or not node or not codex.is_file():
        raise RuntimeError('python3、node 和 codex 必须均可执行。')
    if not os.environ.get('https_proxy'):
        raise RuntimeError('当前 Codex CLI 需要本机 HTTPS 代理；拒绝生成无代理的后台配置。')
    proxy_entries = []
    for name in ('http_proxy', 'https_proxy', 'all_proxy'):
        value = os.environ.get(name, '')
        if not value:
            continue
        parsed = urlsplit(value)
        if parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.username or parsed.password:
            raise RuntimeError('只允许写入无账号的本机代理地址。')
        proxy_entries.append(f'<key>{name}</key><string>{escape(value)}</string>')
    proxy_entries.append('<key>no_proxy</key><string>localhost,127.0.0.1,::1</string>')
    sap_entries = []
    workspace = sap_workspace or os.environ.get('AI_TICKET_SAP_WORKSPACE')
    user = ps4_user or os.environ.get('AI_TICKET_PS4_APPROVED_USER')
    if mode == 'run' and (not workspace or not user):
        raise RuntimeError('运行模式必须明确配置公司 SAP 工作区及与 root profile 一致的 PS4 固定账号。')
    if workspace:
        root = Path(workspace).resolve(strict=True)
        for relative in ['SAP 万能工具箱/scripts/sap-eyes/cli.mjs', 'SAP 万能工具箱/scripts/sap-eyes/policy.mjs',
                'local-sap-security/local-approval-signer.mjs', 'local-sap-security/signed-approval.mjs']:
            if not (root / relative).is_file(): raise RuntimeError('公司 SAP 只读工具或签名器缺失。')
        sap_entries.append('<key>AI_TICKET_SAP_WORKSPACE</key><string>' + escape(str(root)) + '</string>')
    if user:
        if not re.fullmatch(r'[A-Z0-9_-]{2,32}', user): raise RuntimeError('PS4 固定账号格式无效。')
        sap_entries.append('<key>AI_TICKET_PS4_APPROVED_USER</key><string>' + escape(user) + '</string>')
    values = {
        '@PYTHON@': escape(python),
        '@PROJECT@': escape(str(ROOT)),
        '@NODE_DIR@': escape(str(Path(node).parent)),
        '@MODE@': '--' + mode,
        '@PROXY_ENV@': '\n    '.join(proxy_entries),
        '@SAP_ENV@': '\n    '.join(sap_entries),
    }
    content = TEMPLATE.read_text()
    for token, value in values.items():
        content = content.replace(token, value)
    plistlib.loads(content.encode())
    return content


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['shadow', 'run'], default='shadow')
    parser.add_argument('--output', type=Path, default=ROOT / 'release/ai-ticket-dispatcher.preview.plist')
    parser.add_argument('--sap-workspace', type=Path)
    parser.add_argument('--ps4-user')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(args.mode, args.sap_workspace, args.ps4_user))
    print(args.output)


if __name__ == '__main__':
    main()
