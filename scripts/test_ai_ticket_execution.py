"""Offline regression runner: memory database, temporary media, no live workers."""
import argparse
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli-path', type=Path, help='Also capture actual CLI requests against a loopback-only fixture.')
    parser.add_argument('--company-workspace', type=Path, help='Run the two existing local signature/ledger test files; never query SAP.')
    args = parser.parse_args()
    environment = os.environ.copy()
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    environment.pop('AI_TICKET_CODEX_TEST_BIN', None)
    if args.cli_path: environment['AI_TICKET_CODEX_TEST_BIN'] = str(args.cli_path.resolve(strict=True))
    result = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_ai_ticket*.py'],
        cwd=ROOT, env=environment, check=False)
    if result.returncode: return result.returncode
    sys.path.insert(0, str(ROOT / 'backend'))
    os.environ.update({'DJANGO_SETTINGS_MODULE': 'config.settings', 'DJANGO_DEBUG': 'true', 'ALLOW_SQLITE': 'true',
        'DJANGO_SECRET_KEY': 'local-execution-boundary-tests-only-0123456789-20261024',
        'SQLITE_PATH': ':memory:', 'POSTGRES_HOST': '', 'DINGTALK_ENABLED': 'false', 'AI_PROVIDER': 'disabled',
        'SAP_CONNECTOR_ENABLED': 'false', 'AI_TICKET_AUTOMATION_ENABLED': 'true'})
    from django.conf import settings
    # Force this even if a developer's .env contains a PostgreSQL connection.
    settings.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
    import django
    django.setup()
    logging.disable(logging.CRITICAL)
    from django.core.management import call_command
    from django.test.utils import get_runner
    with tempfile.TemporaryDirectory(prefix='sapdesk-execution-tests-') as folder:
        settings.MEDIA_ROOT = folder
        failures = get_runner(settings)(verbosity=1, interactive=False).run_tests(['desk', 'assistant'])
        if failures: return 1
        call_command('makemigrations', check=True, dry_run=True, verbosity=1)
    if args.company_workspace:
        base = args.company_workspace.resolve(strict=True) / 'local-sap-security/test'
        result = subprocess.run(['node', '--test', str(base / 'signed-approval.test.mjs'),
            str(base / 'local-approval-signer.test.mjs')], cwd=ROOT, check=False)
        if result.returncode: return result.returncode
    return 0


if __name__ == '__main__': raise SystemExit(main())
