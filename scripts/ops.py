"""Host-only SAP Desk backup and monitoring. Standard library; no business data in alerts."""
import argparse
import base64
import fcntl
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import ssl
import subprocess
import tarfile
import time
import tempfile
import urllib.parse
import urllib.request
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'ops-state'
DAILY = ROOT / 'backups' / 'daily'
COMPOSE = ['docker', 'compose', '--env-file', '.env.production']


def run(args, timeout=90, **kwargs):
    return subprocess.run(args, cwd=ROOT, check=True, timeout=timeout, stderr=subprocess.DEVNULL,
                          stdout=kwargs.pop('stdout', subprocess.PIPE), **kwargs)


def read_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    tmp.chmod(0o600)
    tmp.replace(path)


@contextmanager
def lock(name, blocking=False):
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE / name).open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda: f.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def prune_daily(now):
    # Only completed directories created by this version; never touch manual/release backups.
    completed = []
    for p in DAILY.iterdir():
        if p.is_symlink() or not p.is_dir() or not re.fullmatch(r'\d{8}T\d{6}Z', p.name):
            continue
        manifest = p / 'manifest.json'
        if manifest.is_symlink() or not manifest.exists():
            continue
        try:
            data = read_json(manifest, {})
            completed_at = data.get('completed_at')
            if (data.get('format') == 'sapdesk-daily-v1' and type(completed_at) in (int, float)
                    and 0 < completed_at <= now + 300 and math.isfinite(completed_at)):
                completed.append((completed_at, p))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    completed.sort(key=lambda item: item[0], reverse=True)
    # An extended outage must not leave only the first new backup after cleanup.
    for completed_at, p in completed[7:]:
        if now - completed_at > 14 * 86400:
            shutil.rmtree(p)


def backup_space_required():
    media = run(COMPOSE + ['run', '--rm', '-T', '--no-deps', '--entrypoint', 'du', 'api', '-sb', '/app/private-media'], timeout=120)
    database = run(COMPOSE + ['exec', '-T', 'db', 'psql', '-U', 'postgres', '-d', 'postgres', '-Atc', "SELECT pg_database_size('sapdesk')"], timeout=30)
    media_bytes = int(media.stdout.split()[0])
    database_bytes = int(database.stdout.strip())
    if media_bytes < 0 or database_bytes < 0:
        raise ValueError('invalid backup size')
    # Keep 1 GiB after a conservative uncompressed snapshot estimate plus 10%.
    return 1024**3 + (media_bytes + database_bytes) * 11 // 10


def backup_counts():
    code = ("import json;from django.db.models import Sum;from desk.models import Ticket,Attachment;"
            "print(json.dumps({'tickets':Ticket.objects.count(),'attachments':Attachment.objects.count(),"
            "'attachment_bytes':Attachment.objects.aggregate(n=Sum('size'))['n'] or 0}))")
    result = run(COMPOSE + ['run', '--rm', '-T', '--no-deps', '--entrypoint', 'python', 'api',
        'manage.py', 'shell', '--no-imports', '-c', code], timeout=60)
    counts = json.loads(result.stdout)
    if set(counts) != {'tickets', 'attachments', 'attachment_bytes'} or any(
            type(value) is not int or value < 0 for value in counts.values()):
        raise ValueError('invalid backup counts')
    return counts


def backup():
    with lock('backup.lock'):
        started = time.time()
        atomic_json(STATE / 'backup.json', {'running': True, 'started_at': started})
        DAILY.mkdir(parents=True, exist_ok=True, mode=0o700)
        dest = DAILY / time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
        work = dest.with_name(dest.name + '.incomplete')
        running = []
        try:
            work.mkdir(mode=0o700)
            available = shutil.disk_usage(DAILY).free
            if available < backup_space_required():
                raise RuntimeError('disk space')
            running = run(COMPOSE + ['ps', '--status', 'running', '--services']).stdout.decode().split()
            running = [s for s in running if s in ['api', 'worker', 'chat-worker']]
            try:
                if running:
                    run(COMPOSE + ['stop', '-t', '30'] + running)
                if shutil.disk_usage(DAILY).free < backup_space_required():
                    raise RuntimeError('disk space')
                counts = backup_counts()
                with (work / 'database.dump').open('wb') as f:
                    run(COMPOSE + ['exec', '-T', 'db', 'pg_dump', '-U', 'postgres', '-d', 'sapdesk', '-Fc'], timeout=600, stdout=f)
                with (work / 'database.dump').open('rb') as f:
                    run(COMPOSE + ['exec', '-T', 'db', 'pg_restore', '--list'], stdin=f)
                # Read the existing attachment volume, even while API is stopped.
                with (work / 'attachments.tar.gz').open('wb') as f:
                    run(COMPOSE + ['run', '--rm', '-T', '--no-deps', '--entrypoint', 'tar', 'api', '-C', '/app/private-media', '-czf', '-', '.'], timeout=600, stdout=f)
            finally:
                if running:
                    run(COMPOSE + ['start'] + running, timeout=120)
                    run(COMPOSE + ['exec', '-T', 'web', 'nginx', '-s', 'reload'])
                    run(['curl', '--fail', '--silent', '--show-error', '--retry', '15', '--retry-all-errors', '--retry-delay', '2', '--max-time', '5', 'https://' + public_host() + '/api/health'], timeout=120)
            with tarfile.open(work / 'attachments.tar.gz', 'r:gz') as archive:
                for member in archive:
                    if member.isfile():
                        f = archive.extractfile(member)
                        while f.read(1024 * 1024):
                            pass
            # Local root-only configuration copy; never sent to DingTalk.
            with tarfile.open(work / 'configuration.tar.gz', 'w:gz', dereference=False) as archive:
                for name in ['.env.production', 'compose.yaml', 'deploy/certs', 'deploy/letsencrypt']:
                    path = ROOT / name
                    if path.exists():
                        archive.add(path, arcname=name)
            files = ['database.dump', 'attachments.tar.gz', 'configuration.tar.gz']
            sums = {name: digest(work / name) for name in files}
            (work / 'SHA256SUMS').write_text(''.join(f'{value}  {name}\n' for name, value in sums.items()))
            for name, value in sums.items():
                assert (work / name).stat().st_size > 0 and digest(work / name) == value
            now = time.time()
            atomic_json(work / 'manifest.json', {'format': 'sapdesk-daily-v1', 'completed_at': now, 'sha256': sums, 'counts': counts})
            work.rename(dest)
            atomic_json(STATE / 'last-success.json', {'completed_at': now, 'directory': str(dest)})
            atomic_json(STATE / 'backup.json', {'running': False, 'ok': True, 'completed_at': now})
            prune_daily(now)
            print('BACKUP_OK', dest.name)
        except BaseException:
            # Preserve incomplete data for diagnosis; do not label it a usable backup.
            atomic_json(STATE / 'backup.json', {'running': False, 'ok': False, 'failed_at': time.time()})
            raise


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def http_open(request):
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=12)


def notify(message):
    token = os.getenv('OPS_DINGTALK_TOKEN', '')
    secret = os.getenv('OPS_DINGTALK_SECRET', '')
    if not token or not secret:
        return False
    stamp = str(int(time.time() * 1000))
    signature = base64.b64encode(hmac.new(secret.encode(), f'{stamp}\n{secret}'.encode(), hashlib.sha256).digest()).decode()
    url = 'https://oapi.dingtalk.com/robot/send?' + urllib.parse.urlencode({'access_token': token, 'timestamp': stamp, 'sign': signature})
    data = json.dumps({'msgtype': 'text', 'text': {'content': 'SAP Desk 运维提醒\n' + message}, 'at': {'isAtAll': False}}).encode()
    try:
        with http_open(urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})) as response:
            return json.load(response).get('errcode') == 0
    except Exception:
        return False  # Never log exceptions containing signed URLs or provider response bodies.


def evaluate(samples, previous, now):
    counts = previous.get('counts', {})
    active = {}
    next_counts = {key: value for key, value in counts.items() if key not in samples}
    for key, (bad, text, required) in samples.items():
        count = counts.get(key, 0) + 1 if bad else 0
        next_counts[key] = count
        if count >= required:
            active[key] = text
    old = previous.get('notified', {})
    changed = set(active) != set(old)
    due = (bool(active) and now - previous.get('sent_at', 0) >= 3600) or changed
    return active, next_counts, due


def ai_samples():
    """Read only bounded server aggregates; never log ticket text or credentials."""
    result = run(COMPOSE + ['exec', '-T', 'api', 'python', 'manage.py', 'ai_tickets', 'health'], timeout=20)
    data = json.loads(result.stdout)
    alerts = data['alerts']
    names = ('offline', 'readiness', 'backlog', 'failure_streak', 'manual')
    if not isinstance(alerts, dict) or any(type(alerts.get(name)) is not bool for name in names):
        raise ValueError('invalid AI monitor response')
    capabilities = data['node']['capabilities']
    if not isinstance(capabilities, dict):
        raise ValueError('invalid AI capability response')
    labels = {'model_cli': '模型程序', 'sap_tools': 'SAP 工具文件', 'production_identity': '固定身份配置', 'proxy': '本机代理'}
    unavailable = [label for key, label in labels.items() if capabilities.get(key) is False]
    return {
        'ai_health_unavailable': (False, 'AI 运行状态暂时无法读取。', 3),
        'ai_executor_offline': (alerts['offline'], 'AI 执行节点超过心跳时限未连接，请检查运行 Mac 和网络。', 1),
        'ai_capabilities': (alerts['readiness'], 'AI 节点依赖异常：' + '、'.join(unavailable or ['请查看运行状态']), 3),
        'ai_backlog': (alerts['backlog'], 'AI 工单等待时间超过阈值，请查看运行状态。', 1),
        'ai_failures': (alerts['failure_streak'], 'AI 连续受阻，请查看运行状态和受阻分类。', 1),
        'ai_manual': (alerts['manual'], 'AI 工单需要管理员处理，请查看运行状态。', 1),
    }


def backup_running_state(backup, in_backup, now):
    if backup.get('running') is not True:
        return False, False
    started = backup.get('started_at')
    recent_start = type(started) in (int, float) and 0 < started <= now+300 and now-started <= 90
    # Tolerate the short race between a lock probe and a newly started backup.
    return bool(in_backup or recent_start), not (in_backup or recent_start)


def publish_backup_health(now, in_backup=False):
    """Expose only sanitized timestamps/booleans to the read-only API mount."""
    backup = read_json(STATE / 'backup.json', {})
    last = read_json(STATE / 'last-success.json', {})
    drill = read_json(STATE / 'last-restore-drill.json', {})
    def timestamp(value):
        return value if type(value) in (int, float) and 0 < value <= now+300 and math.isfinite(value) else None
    def boolean(value):
        return value if type(value) is bool else None
    running, interrupted = backup_running_state(backup, in_backup, now)
    payload = {'schema': 'sapdesk-backup-health/v1', 'generated_at': now,
        'running': running, 'last_backup_ok': False if interrupted else boolean(backup.get('ok')),
        'last_success_at': timestamp(last.get('completed_at')),
        'last_drill_at': timestamp(drill.get('completed_at')), 'last_drill_ok': boolean(drill.get('ok'))}
    directory = STATE / 'api-health'
    if directory.is_symlink():
        raise OSError('status directory is a symlink')
    directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    directory.chmod(0o755)
    fd, temporary = tempfile.mkstemp(prefix='.backup-health-', dir=directory)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(payload, stream, ensure_ascii=False)
            stream.flush(); os.fsync(stream.fileno()); os.fchmod(stream.fileno(), 0o644)
        os.replace(temporary, directory / 'backup.json')
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def drill_samples(drill, now):
    completed = drill.get('completed_at')
    valid_time = type(completed) in (int, float) and 0 < completed <= now+300 and math.isfinite(completed)
    return {
        'restore_drill_failed': (drill.get('ok') is False, '最近一次隔离恢复演练未通过，请检查运维记录。', 1),
        'restore_drill_stale': (drill.get('ok') is True and valid_time and now-completed > 30*86400,
                                '上次隔离恢复演练已超过 30 天，请安排复核。', 1),
    }


def public_host():
    host = ''
    for line in (ROOT / '.env.production').read_text().splitlines():
        if line.startswith('APP_DOMAIN='):
            host = line.partition('=')[2].strip().strip('\"\'')
    if not re.fullmatch(r'[A-Za-z0-9.-]+', host):
        raise RuntimeError('invalid host')
    return host


def monitor():
    with lock('monitor.lock'):
        now = time.time()
        previous = read_json(STATE / 'monitor.json', {})
        samples = {}
        memory = {line.split(':')[0]: int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()}
        memory_pct = 100 * (1 - memory['MemAvailable'] / memory['MemTotal'])
        samples['memory'] = (memory_pct >= 85, f'内存持续偏高：{memory_pct:.0f}%', 5)
        disk = shutil.disk_usage(ROOT)
        disk_pct = 100 * disk.used / disk.total
        samples['disk'] = (disk_pct >= 80, f'磁盘占用达到 {disk_pct:.0f}%，请检查附件和备份空间。', 1)
        cpu = list(map(int, Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
        old_cpu = previous.get('cpu')
        if old_cpu and sum(cpu) > sum(old_cpu):
            total = sum(cpu) - sum(old_cpu)
            idle = cpu[3] + cpu[4] - old_cpu[3] - old_cpu[4]
            pct = 100 * (1 - idle / total)
            samples['cpu'] = (pct >= 85, f'CPU 持续偏高：{pct:.0f}%', 5)
        in_backup = False
        try:
            with lock('backup.lock'):
                pass
        except BlockingIOError:
            in_backup = True
        host = public_host()
        ai_unavailable = False
        if not in_backup:
            try:
                with http_open('https://' + host + '/api/health') as response:
                    healthy = json.load(response).get('status') == 'ok'
            except Exception:
                healthy = False
            samples['https'] = (not healthy, 'HTTPS 页面或数据库健康检查连续失败。', 3)
            try:
                containers = ['sapdesk-api-1', 'sapdesk-worker-1', 'sapdesk-web-1', 'sapdesk-db-1', 'sapdesk-chat-worker-1']
                rows = [json.loads(line) for line in run(['docker', 'inspect', '--format', '{{json .State}}'] + containers).stdout.decode().splitlines()]
                good = len(rows) == len(containers) and all(r['Running'] and r.get('Health', {}).get('Status', 'healthy') == 'healthy' for r in rows)
            except Exception:
                good = False
            samples['containers'] = (not good, '平台容器停止或健康检查异常。', 3)
            try:
                samples.update(ai_samples())
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
                ai_unavailable = True
                samples['ai_health_unavailable'] = (True, 'AI 运行状态连续无法读取，请检查服务。', 3)
        last = read_json(STATE / 'last-success.json', {})
        samples['backup_stale'] = (now - last.get('completed_at', 0) > 30*3600, '超过 30 小时没有成功备份。', 1)
        backup_state = read_json(STATE / 'backup.json', {})
        _running, interrupted = backup_running_state(backup_state, in_backup, now)
        samples['backup_failed'] = (backup_state.get('ok') is False or interrupted, '最近一次数据库/附件备份失败，请检查备份任务。', 1)
        samples.update(drill_samples(read_json(STATE / 'last-restore-drill.json', {}), now))
        try:
            publish_backup_health(now, in_backup)
            samples['backup_health_unavailable'] = (False, '备份状态无法提供给平台。', 3)
        except (OSError, ValueError, TypeError):
            samples['backup_health_unavailable'] = (True, '备份状态无法提供给平台，请检查本机运维任务。', 3)
        cert = ROOT / 'deploy/certs/current/fullchain.pem'
        result = subprocess.run(['openssl', 'x509', '-checkend', str(48*3600), '-noout', '-in', str(cert)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        samples['certificate'] = (result.returncode != 0, 'HTTPS 证书有效期不足 48 小时或无法读取，请检查自动续期。', 1)
        renewal = subprocess.run(['systemctl', 'is-failed', '--quiet', 'sapdesk-cert-renew.service']).returncode == 0
        samples['renewal'] = (renewal, 'HTTPS 证书自动续期任务失败。', 1)
        active, counts, due = evaluate(samples, previous, now)
        # Preserve existing service incidents during planned backup downtime.
        if in_backup or ai_unavailable:
            for key, value in previous.get('notified', {}).items():
                if (in_backup and key in ['https', 'containers']) or (key.startswith('ai_') and key != 'ai_health_unavailable'):
                    active[key] = value
            if in_backup and 'ai_health_unavailable' in previous.get('notified', {}):
                active['ai_health_unavailable'] = previous['notified']['ai_health_unavailable']
            due = set(active) != set(previous.get('notified', {})) or (bool(active) and now-previous.get('sent_at',0)>=3600)
        if due:
            resolved = set(previous.get('notified', {})) - set(active)
            lines = list(active.values())
            if resolved:
                lines.append('已恢复：' + '、'.join(sorted(resolved)))
            if not active:
                lines = ['此前告警已全部恢复。']
            if notify('\n'.join(lines)):
                previous['notified'] = active
                previous['sent_at'] = now
            else:
                print('ALERT_DELIVERY_FAILED_OR_NOT_CONFIGURED')
        previous.update(counts=counts, cpu=cpu, checked_at=now, active=active)
        atomic_json(STATE / 'monitor.json', previous)
        print('MONITOR_OK incidents=' + str(len(active)) + ' channel=' + ('configured' if os.getenv('OPS_DINGTALK_TOKEN') and os.getenv('OPS_DINGTALK_SECRET') else 'not-configured'))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['backup', 'monitor', 'test-alert'])
    action = parser.parse_args().action
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(InterruptedError()))
    try:
        if action == 'backup': backup()
        elif action == 'monitor': monitor()
        else:
            if not notify('管理员告警渠道测试：收到此消息表示机器人接口已接受测试消息。'):
                raise RuntimeError('channel')
            print('TEST_ALERT_ACCEPTED')
    except BlockingIOError:
        print('TASK_ALREADY_RUNNING')
    except BaseException as exc:
        if action == 'monitor':
            failure = read_json(STATE / 'monitor-error.json', {})
            if time.time() - failure.get('sent_at', 0) >= 3600 and notify('运维监测任务执行失败，请检查服务器日志。'):
                atomic_json(STATE / 'monitor-error.json', {'sent_at': time.time()})
        print('OPS_TASK_FAILED', type(exc).__name__)
        raise SystemExit(1) from None

if __name__ == '__main__':
    main()
