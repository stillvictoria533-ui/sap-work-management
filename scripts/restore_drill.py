"""Verify a completed local backup or restore it into disposable Docker resources.

The drill never mounts production volumes or publishes ports. Its network is
internal, outbound notifications/AI are disabled, and cleanup checks labels.
"""
import argparse
import hashlib
import hmac
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

from ops import STATE, atomic_json

FILES = ('database.dump', 'attachments.tar.gz', 'configuration.tar.gz')
LABEL = 'com.sapdesk.restore-drill'
IMAGE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,200}$')


class DrillError(Exception):
    pass


def regular_file(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(descriptor)
            raise DrillError('backup_file_invalid')
        return os.fdopen(descriptor, 'rb')
    except OSError:
        raise DrillError('backup_file_unavailable') from None


def limited_json(path, limit=65536):
    with regular_file(path) as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise DrillError('manifest_invalid')
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise DrillError('manifest_invalid') from None
    if not isinstance(value, dict):
        raise DrillError('manifest_invalid')
    return value


def verify_backup(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir() or directory.name.endswith('.incomplete'):
        raise DrillError('backup_directory_invalid')
    manifest = limited_json(directory / 'manifest.json')
    if manifest.get('format') != 'sapdesk-daily-v1' or not isinstance(manifest.get('sha256'), dict):
        raise DrillError('manifest_invalid')
    completed_at = manifest.get('completed_at')
    if (type(completed_at) not in (int, float) or completed_at <= 0
            or completed_at > time.time() + 300 or not math.isfinite(completed_at)):
        raise DrillError('manifest_invalid')
    sums = manifest['sha256']
    if set(sums) != set(FILES) or any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value) for value in sums.values()):
        raise DrillError('manifest_invalid')
    with regular_file(directory / 'SHA256SUMS') as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise DrillError('checksum_file_invalid')
    try:
        lines = raw.decode('ascii').splitlines()
        expected = {}
        for line in lines:
            match = re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9._-]+)', line)
            if not match or match[2] in expected:
                raise ValueError()
            expected[match[2]] = match[1]
    except (ValueError, UnicodeError):
        raise DrillError('checksum_file_invalid') from None
    if expected != sums:
        raise DrillError('checksum_file_invalid')
    for name in FILES:
        digest = hashlib.sha256()
        with regular_file(directory / name) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        if not hmac.compare_digest(digest.hexdigest(), sums[name]):
            raise DrillError('backup_checksum_mismatch')
    with regular_file(directory / 'database.dump') as stream:
        if stream.read(5) != b'PGDMP':
            raise DrillError('database_header_invalid')
    file_count = 0
    seen = set()
    try:
        with regular_file(directory / 'attachments.tar.gz') as stream, tarfile.open(fileobj=stream, mode='r:gz') as archive:
            for member in archive:
                name = PurePosixPath(member.name)
                if (name.is_absolute() or '..' in name.parts or '\\' in member.name or
                        not (member.isfile() or member.isdir())):
                    raise DrillError('attachment_archive_unsafe')
                if member.isfile():
                    if str(name) in seen:
                        raise DrillError('attachment_archive_unsafe')
                    seen.add(str(name)); file_count += 1
                    if file_count > 200000:
                        raise DrillError('attachment_archive_too_many_files')
                    with archive.extractfile(member) as content:
                        for _ in iter(lambda: content.read(1024 * 1024), b''):
                            pass
    except (tarfile.TarError, OSError, EOFError):
        raise DrillError('attachment_archive_invalid') from None
    counts = manifest.get('counts')
    if counts is not None and (not isinstance(counts, dict) or set(counts) != {'tickets', 'attachments', 'attachment_bytes'}
            or any(type(value) is not int or not 0 <= value <= 9223372036854775807 for value in counts.values())):
        raise DrillError('backup_counts_invalid')
    return {'archive_files': file_count, 'expected_counts': counts}


def docker(args, *, stage, timeout=60, stdin=None, input_data=None, capture=False, check=True):
    try:
        result = subprocess.run(['docker', *args], stdin=stdin, input=input_data, stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise DrillError(stage + '_failed') from None
    if check and result.returncode:
        raise DrillError(stage + '_failed')
    return result


def inspect_owned(kind, name, token):
    field = '{{index .Config.Labels "' + LABEL + '"}}' if kind == 'container' else '{{index .Labels "' + LABEL + '"}}'
    result = docker([kind, 'inspect', '--format', field, name], stage='cleanup_inspect', capture=True, check=False)
    if result.returncode:
        # Only a reachable Docker daemon can distinguish an absent resource from a failed inspection.
        docker(['info', '--format', '{{.ID}}'], stage='cleanup_daemon')
        return False
    if result.stdout.decode().strip() != token:
        raise DrillError('cleanup_identity_mismatch')
    return True


def cleanup_resource(kind, name, token):
    if not re.fullmatch(r'sapdesk-drill-[0-9a-f]{16}-(db|network|pg|media|migrate|extract|smoke)', name):
        raise DrillError('cleanup_name_invalid')
    if not inspect_owned(kind, name, token):
        return
    args = ['rm', '-f', name] if kind == 'container' else [kind, 'rm', name]
    docker(args, stage='cleanup')


EXTRACT = r'''import os,sys,tarfile
with tarfile.open(fileobj=sys.stdin.buffer,mode="r|gz") as archive:
    archive.extractall('/restore',filter='data')
for base,dirs,files in os.walk('/restore'):
    os.chown(base,10001,10001)
    for name in dirs+files: os.chown(os.path.join(base,name),10001,10001)
'''

SMOKE = r'''import json,os,uuid
from django.db.models import Sum
from desk.models import User,Ticket,Attachment
from rest_framework.test import APIClient
expected=json.loads(os.environ['DRILL_EXPECTED_COUNTS'])
counts={'tickets':Ticket.objects.count(),'attachments':Attachment.objects.count(),
        'attachment_bytes':Attachment.objects.aggregate(n=Sum('size'))['n'] or 0}
if expected is not None and counts!=expected: raise RuntimeError('backup_counts_mismatch')
name='drill-'+uuid.uuid4().hex;email=name+'@invalid.test';password=os.environ['DRILL_SMOKE_PASSWORD']
user=User.objects.create_user(username=email,email=email,name='恢复演练',role='admin',
                              password=password,must_change_password=False)
client=APIClient();response=client.post('/api/auth/login',{'email':user.email,'password':password},format='json')
if response.status_code!=200 or client.get('/api/auth/session').json()['user']['id']!=user.pk: raise RuntimeError('login_failed')
listing=client.get('/api/tickets')
if listing.status_code!=200 or listing.data['count']!=counts['tickets']: raise RuntimeError('ticket_listing_failed')
attachment=Attachment.objects.order_by('id').first();checked=False
if attachment:
    response=client.get('/api/attachments/'+str(attachment.pk)+'/download')
    if response.status_code!=200: raise RuntimeError('attachment_download_failed')
    size=sum(len(chunk) for chunk in response.streaming_content)
    if size!=attachment.size: raise RuntimeError('attachment_size_mismatch')
    checked=True
print('DRILL_RESULT='+json.dumps({'tickets':counts['tickets'],'attachments':counts['attachments'],
      'expected_counts_match':None if expected is None else True,'login':True,'attachment_download':checked}))
'''


def restore_drill(directory, verified, api_image='sapdesk-api:latest', postgres_image='postgres:17-alpine'):
    if not IMAGE.fullmatch(api_image) or not IMAGE.fullmatch(postgres_image):
        raise DrillError('image_name_invalid')
    docker(['info', '--format', '{{.ID}}'], stage='docker_unavailable')
    token = secrets.token_hex(8)
    prefix = 'sapdesk-drill-' + token
    names = {key: prefix + '-' + key for key in ['db', 'network', 'pg', 'media', 'migrate', 'extract', 'smoke']}
    resources = [('container', names[key]) for key in ['smoke', 'migrate', 'extract', 'db']] + [
        ('network', names['network']), ('volume', names['pg']), ('volume', names['media'])]
    # Never attach an existing resource, even if its name happens to collide.
    for kind, name in resources:
        result = docker([kind, 'inspect', name], stage='resource_check', check=False)
        if result.returncode == 0:
            raise DrillError('resource_exists')
    password = secrets.token_urlsafe(32)
    smoke_password = secrets.token_urlsafe(32)
    assert re.fullmatch(r'[A-Za-z0-9_-]+', password)
    cleanup_errors = []
    try:
        with tempfile.TemporaryDirectory(prefix='sapdesk-restore-drill-') as folder:
            folder = Path(folder); folder.chmod(0o700)
            pg_env = folder/'postgres.env'; api_env = folder/'api.env'
            pg_env.write_text('POSTGRES_USER=postgres\nPOSTGRES_DB=postgres\nPOSTGRES_PASSWORD='+password+'\nPGDATA=/var/lib/postgresql/data/pgdata\n')
            api_env.write_text('\n'.join(['DJANGO_DEBUG=true', 'DJANGO_SECRET_KEY='+secrets.token_urlsafe(48),
                'DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,testserver', 'POSTGRES_HOST=db', 'POSTGRES_DB=sapdesk',
                'POSTGRES_USER=sapdesk', 'POSTGRES_PASSWORD='+password, 'MEDIA_ROOT=/app/private-media',
                'DINGTALK_ENABLED=false', 'AI_PROVIDER=disabled', 'AI_TICKET_AUTOMATION_ENABLED=false',
                'DRILL_SMOKE_PASSWORD='+smoke_password, 'DRILL_EXPECTED_COUNTS='+json.dumps(verified['expected_counts'], separators=(',', ':'))])+'\n')
            pg_env.chmod(0o600); api_env.chmod(0o600)
            docker(['network', 'create', '--internal', '--label', LABEL+'='+token, names['network']], stage='network_create')
            internal = docker(['network', 'inspect', '--format', '{{.Internal}}', names['network']], stage='network_check', capture=True)
            if internal.stdout.decode().strip() != 'true': raise DrillError('network_not_internal')
            for key in ['pg', 'media']:
                docker(['volume', 'create', '--label', LABEL+'='+token, names[key]], stage='volume_create')
            docker(['run', '-d', '--pull=never', '--name', names['db'], '--label', LABEL+'='+token,
                '--network', names['network'], '--network-alias', 'db', '--mount', 'type=volume,source='+names['pg']+',target=/var/lib/postgresql/data',
                '--env-file', str(pg_env), '--pids-limit', '128', '--memory', '512m', '--cpus', '0.75', postgres_image], stage='postgres_start')
            ports = json.loads(docker(['inspect', '--format', '{{json .NetworkSettings.Ports}}', names['db']], stage='port_check', capture=True).stdout)
            if ports and any(value is not None for value in ports.values()): raise DrillError('port_exposed')
            for _ in range(40):
                ready = docker(['exec', names['db'], 'pg_isready', '-U', 'postgres', '-d', 'postgres'], stage='postgres_ready', check=False)
                if ready.returncode == 0: break
                time.sleep(1)
            else: raise DrillError('postgres_ready_failed')
            sql = f'CREATE ROLE sapdesk LOGIN PASSWORD \'{password}\';\nCREATE DATABASE sapdesk OWNER sapdesk;\n'.encode()
            docker(['exec', '-i', names['db'], 'psql', '-U', 'postgres', '-d', 'postgres', '-v', 'ON_ERROR_STOP=1'],
                   stage='database_prepare', input_data=sql)
            with regular_file(Path(directory)/'database.dump') as stream:
                docker(['exec', '-i', names['db'], 'pg_restore', '-U', 'postgres', '--role=sapdesk', '--no-owner', '--no-privileges', '-d', 'sapdesk'],
                       stage='database_restore', timeout=900, stdin=stream)
            with regular_file(Path(directory)/'attachments.tar.gz') as stream:
                docker(['run', '--rm', '-i', '--pull=never', '--name', names['extract'], '--label', LABEL+'='+token,
                    '--network', 'none', '--user', '0', '--read-only', '--mount', 'type=volume,source='+names['media']+',target=/restore',
                    '--entrypoint', 'python', api_image, '-c', EXTRACT], stage='attachment_restore', timeout=900, stdin=stream)
            common = ['--rm', '-i', '--pull=never', '--label', LABEL+'='+token, '--network', names['network'],
                '--read-only', '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m', '--cap-drop', 'ALL', '--pids-limit', '128',
                '--memory', '384m', '--cpus', '0.5', '--env-file', str(api_env),
                '--mount', 'type=volume,source='+names['media']+',target=/app/private-media,readonly']
            docker(['run', '--name', names['migrate'], *common, api_image, 'python', 'manage.py', 'migrate', '--noinput'],
                   stage='clone_migrate', timeout=900)
            result = docker(['run', '--name', names['smoke'], *common, api_image, 'python', 'manage.py', 'shell', '--no-imports'],
                            stage='clone_smoke', timeout=120, input_data=SMOKE.encode(), capture=True)
            lines = result.stdout.decode().splitlines()
            line = next((line for line in lines if line.startswith('DRILL_RESULT=')), None)
            if not line: raise DrillError('clone_smoke_invalid')
            summary = json.loads(line.partition('=')[2])
            if summary.get('login') is not True or type(summary.get('tickets')) is not int or type(summary.get('attachments')) is not int:
                raise DrillError('clone_smoke_invalid')
            return summary
    finally:
        for kind, name in resources:
            try: cleanup_resource(kind, name, token)
            except DrillError as error: cleanup_errors.append(str(error))
        if cleanup_errors:
            raise DrillError('cleanup_unconfirmed')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['verify', 'drill'])
    parser.add_argument('backup', type=Path)
    parser.add_argument('--api-image', default='sapdesk-api:latest')
    parser.add_argument('--postgres-image', default='postgres:17-alpine')
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(InterruptedError()))
    try:
        verified = verify_backup(args.backup)
        if args.operation == 'verify':
            print(json.dumps({'outcome': 'verified', 'archive_files': verified['archive_files'],
                              'counts_available': verified['expected_counts'] is not None}))
            return 0
        summary = restore_drill(args.backup, verified, args.api_image, args.postgres_image)
        atomic_json(STATE/'last-restore-drill.json', {'completed_at': time.time(), 'ok': True,
            'counts_verified': summary['expected_counts_match'] is True,
            'attachment_verified': summary['attachment_download']})
        print(json.dumps({'outcome': 'passed', **summary}))
        return 0
    except (DrillError, OSError, ValueError, TypeError, InterruptedError, KeyboardInterrupt) as error:
        code = str(error) if isinstance(error, DrillError) else 'drill_interrupted_or_failed'
        if args.operation == 'drill':
            try:
                atomic_json(STATE/'last-restore-drill.json', {'completed_at': time.time(), 'ok': False, 'reason': code})
            except OSError:
                code = 'drill_status_write_failed'
        print(json.dumps({'outcome': 'failed', 'reason': code}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
