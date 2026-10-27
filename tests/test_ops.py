import json
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import tarfile
import time
import unittest
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ops
import restore_drill

class OpsTests(unittest.TestCase):
    def test_alert_threshold_cooldown_and_recovery(self):
        active, counts, due = ops.evaluate({'cpu': (True, 'high', 5)}, {'counts': {'cpu': 3}}, 5000)
        self.assertEqual(active, {})
        self.assertFalse(due)
        active, counts, due = ops.evaluate({'cpu': (True, 'high', 5)}, {'counts': counts}, 5060)
        self.assertEqual(active, {'cpu': 'high'})
        self.assertTrue(due)
        old={'counts': counts,'notified':active,'sent_at':5060}
        self.assertFalse(ops.evaluate({'cpu': (True, 'high', 5)},old,5120)[2])
        self.assertTrue(ops.evaluate({'cpu': (True, 'high', 5)},old,9000)[2])
        self.assertTrue(ops.evaluate({'cpu': (False, 'high', 5)},old,5120)[2])

    def test_missing_ai_sample_keeps_its_consecutive_count(self):
        active, counts, due = ops.evaluate({'cpu': (False, 'high', 5)}, {'counts': {'ai_capabilities': 2}}, 5000)
        self.assertEqual(counts['ai_capabilities'], 2)

    def test_ai_samples_use_fixed_messages_and_reject_malformed_state(self):
        payload = {'alerts': {'offline': True, 'readiness': True, 'backlog': False, 'failure_streak': False, 'manual': False},
                   'node': {'capabilities': {'model_cli': False, 'sap_tools': True, 'production_identity': True, 'proxy': True}},
                   'ticket_title': 'PRIVATE_TICKET'}
        with patch.object(ops, 'run', return_value=Mock(stdout=json.dumps(payload).encode())):
            samples = ops.ai_samples()
        self.assertTrue(samples['ai_executor_offline'][0])
        self.assertIn('模型程序', samples['ai_capabilities'][1])
        self.assertNotIn('PRIVATE_TICKET', str(samples))
        payload['alerts']['offline'] = 'true'
        with patch.object(ops, 'run', return_value=Mock(stdout=json.dumps(payload).encode())):
            with self.assertRaises(ValueError): ops.ai_samples()

    def test_public_backup_report_contains_only_safe_metadata(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ops, 'STATE', Path(tmp)):
            (ops.STATE/'last-success.json').write_text(json.dumps({'completed_at': 100, 'directory': '/private/backup'}))
            (ops.STATE/'backup.json').write_text(json.dumps({'running': False, 'ok': True, 'password': 'SECRET'}))
            (ops.STATE/'last-restore-drill.json').write_text(json.dumps({'completed_at': 150, 'ok': True, 'ticket': 'PRIVATE'}))
            ops.publish_backup_health(200)
            path = ops.STATE/'api-health'/'backup.json'
            data = json.loads(path.read_text())
            self.assertEqual(data['last_success_at'], 100)
            self.assertTrue(data['last_drill_ok'])
            self.assertNotIn('SECRET', path.read_text())
            self.assertNotIn('PRIVATE', path.read_text())
            self.assertNotIn('/private/backup', path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_public_backup_report_rejects_a_symlink_directory(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ops, 'STATE', Path(tmp)):
            outside = ops.STATE/'outside'; outside.mkdir()
            (ops.STATE/'api-health').symlink_to(outside)
            with self.assertRaises(OSError): ops.publish_backup_health(200)
            self.assertFalse((outside/'backup.json').exists())

    def test_interrupted_backup_is_not_reported_as_running_forever(self):
        self.assertEqual(ops.backup_running_state({'running': True, 'started_at': 100}, False, 120), (True, False))
        self.assertEqual(ops.backup_running_state({'running': True, 'started_at': 100}, True, 1000), (True, False))
        self.assertEqual(ops.backup_running_state({'running': True, 'started_at': 100}, False, 1000), (False, True))
        with tempfile.TemporaryDirectory() as tmp, patch.object(ops, 'STATE', Path(tmp)):
            (ops.STATE/'backup.json').write_text(json.dumps({'running': True, 'started_at': 100}))
            ops.publish_backup_health(1000, in_backup=False)
            report = json.loads((ops.STATE/'api-health'/'backup.json').read_text())
            self.assertFalse(report['running'])
            self.assertFalse(report['last_backup_ok'])

    def test_restore_drill_alerts_do_not_leak_operational_data(self):
        self.assertFalse(ops.drill_samples({}, 1000)['restore_drill_stale'][0])
        self.assertTrue(ops.drill_samples({'ok': False, 'reason': 'SECRET'}, 1000)['restore_drill_failed'][0])
        old = ops.drill_samples({'ok': True, 'completed_at': 100}, 31*86400)
        self.assertTrue(old['restore_drill_stale'][0])
        self.assertNotIn('SECRET', str(old))

    def test_only_expired_completed_managed_backups_are_removed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ops, 'DAILY', Path(tmp)):
            now=time.time()
            for name, age in [('20261001T030000Z',15)] + [(f'202610{day:02}T030000Z', day-1) for day in range(2, 10)]:
                p=Path(tmp)/name;p.mkdir();(p/'manifest.json').write_text(json.dumps({'format':'sapdesk-daily-v1','completed_at':now-age*86400}))
            manual=Path(tmp)/'manual';manual.mkdir()
            incomplete=Path(tmp)/'20260901T030000Z.incomplete';incomplete.mkdir()
            outside=Path(tmp)/'outside';outside.mkdir()
            (Path(tmp)/'20260901T030000Z').symlink_to(outside)
            ops.prune_daily(now)
            self.assertFalse((Path(tmp)/'20261001T030000Z').exists())
            self.assertTrue((Path(tmp)/'20261002T030000Z').exists())
            self.assertTrue(manual.exists() and incomplete.exists() and outside.exists())

    def test_cleanup_preserves_seven_completed_backups_after_an_outage(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ops, 'DAILY', Path(tmp)):
            now = time.time()
            for index in range(5):
                p = ops.DAILY/f'202609{index+1:02}T030000Z'; p.mkdir()
                (p/'manifest.json').write_text(json.dumps({'format': 'sapdesk-daily-v1', 'completed_at': now-(15+index)*86400}))
            ops.prune_daily(now)
            self.assertEqual(len(list(ops.DAILY.iterdir())), 5)

    def test_backup_resumes_services_after_dump_or_partial_stop_failure(self):
        for stage in ['stop', 'pg_dump']:
            with tempfile.TemporaryDirectory() as tmp, patch.object(ops,'STATE',Path(tmp)/'state'), patch.object(ops,'DAILY',Path(tmp)/'daily'):
                calls=[]
                def command(args, **kwargs):
                    calls.append(args)
                    if 'ps' in args: return Mock(stdout=b'api\nworker\nchat-worker\n')
                    if 'du' in args: return Mock(stdout=b'128 /app/private-media')
                    if 'psql' in args: return Mock(stdout=b'1024')
                    if 'shell' in args: return Mock(stdout=b'{"tickets":0,"attachments":0,"attachment_bytes":0}')
                    if stage in args: raise subprocess.CalledProcessError(1,args)
                    return Mock(stdout=b'')
                with patch.object(ops,'public_host',return_value='127.0.0.1'), patch.object(ops,'run',side_effect=command), patch.object(ops.shutil,'disk_usage',return_value=Mock(free=2**40)):
                    with self.assertRaises(subprocess.CalledProcessError): ops.backup()
                self.assertTrue(any('start' in c and 'api' in c and 'worker' in c for c in calls))
                self.assertTrue(any('stop' in c and 'chat-worker' in c for c in calls))
                self.assertTrue(any('start' in c and 'chat-worker' in c for c in calls))
                self.assertTrue(any('reload' in c for c in calls))
                self.assertTrue(any('curl' in c for c in calls))
                self.assertFalse(ops.read_json(ops.STATE/'backup.json',{})['ok'])
                self.assertFalse((ops.STATE/'last-success.json').exists())

    def test_backup_rejects_insufficient_space_before_stopping_services(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ops, 'STATE', Path(tmp)/'state'), patch.object(ops, 'DAILY', Path(tmp)/'daily'):
            calls = []
            def command(args, **kwargs):
                calls.append(args)
                if 'du' in args: return Mock(stdout=b'1000000 /app/private-media')
                if 'psql' in args: return Mock(stdout=b'1000000')
                return Mock(stdout=b'api\nworker\n')
            with patch.object(ops, 'run', side_effect=command), patch.object(ops.shutil, 'disk_usage', return_value=Mock(free=1024**3)):
                with self.assertRaisesRegex(RuntimeError, 'disk space'): ops.backup()
            self.assertFalse(any('stop' in args for args in calls))
            self.assertFalse(ops.read_json(ops.STATE/'backup.json', {})['ok'])

    def test_completed_backup_records_counts_and_passes_integrity_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'.env.production').write_text('SYNTHETIC_ONLY=yes')
            with patch.object(ops, 'ROOT', root), patch.object(ops, 'STATE', root/'ops-state'), patch.object(ops, 'DAILY', root/'backups'/'daily'):
                def command(args, **kwargs):
                    if 'ps' in args: return Mock(stdout=b'api\nworker\nchat-worker\n')
                    if 'du' in args: return Mock(stdout=b'128 /app/private-media')
                    if 'psql' in args: return Mock(stdout=b'1024')
                    if 'shell' in args: return Mock(stdout=b'{"tickets":1,"attachments":1,"attachment_bytes":5}')
                    if 'pg_dump' in args: kwargs['stdout'].write(b'PGDMP synthetic dump')
                    if 'tar' in args:
                        with tarfile.open(fileobj=kwargs['stdout'], mode='w:gz') as archive:
                            data=b'hello'; member=tarfile.TarInfo('1/file'); member.size=len(data); archive.addfile(member,io.BytesIO(data))
                    return Mock(stdout=b'')
                with patch.object(ops, 'run', side_effect=command), patch.object(ops, 'public_host', return_value='127.0.0.1'), \
                     patch.object(ops.shutil, 'disk_usage', return_value=Mock(free=2**40)), contextlib.redirect_stdout(io.StringIO()):
                    ops.backup()
                backups = list(ops.DAILY.iterdir())
                self.assertEqual(len(backups), 1)
                verified = restore_drill.verify_backup(backups[0])
                self.assertEqual(verified['expected_counts'], {'tickets': 1, 'attachments': 1, 'attachment_bytes': 5})
                self.assertEqual(verified['archive_files'], 1)
                self.assertTrue(ops.read_json(ops.STATE/'backup.json', {})['ok'])

    def test_no_credentials_no_outbound_alert(self):
        with patch.dict(ops.os.environ,{},clear=True), patch.object(ops,'http_open') as request:
            self.assertFalse(ops.notify('test'));request.assert_not_called()

    def test_provider_rejection_is_not_success(self):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=b'{"errcode":310000}'
        with patch.dict(ops.os.environ,{'OPS_DINGTALK_TOKEN':'test','OPS_DINGTALK_SECRET':'test'}),patch.object(ops,'http_open',return_value=response):
            self.assertFalse(ops.notify('test'))

if __name__=='__main__':unittest.main()
