import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ops

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

    def test_only_expired_completed_managed_backups_are_removed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ops, 'DAILY', Path(tmp)):
            now=time.time()
            for name, age in [('20261001T030000Z',15),('20261012T030000Z',1)]:
                p=Path(tmp)/name;p.mkdir();(p/'manifest.json').write_text(json.dumps({'format':'sapdesk-daily-v1','completed_at':now-age*86400}))
            manual=Path(tmp)/'manual';manual.mkdir()
            incomplete=Path(tmp)/'20260901T030000Z.incomplete';incomplete.mkdir()
            outside=Path(tmp)/'outside';outside.mkdir()
            (Path(tmp)/'20260901T030000Z').symlink_to(outside)
            ops.prune_daily(now)
            self.assertFalse((Path(tmp)/'20261001T030000Z').exists())
            self.assertTrue((Path(tmp)/'20261012T030000Z').exists())
            self.assertTrue(manual.exists() and incomplete.exists() and outside.exists())

    def test_backup_resumes_services_after_dump_or_partial_stop_failure(self):
        for stage in ['stop', 'pg_dump']:
            with tempfile.TemporaryDirectory() as tmp, patch.object(ops,'STATE',Path(tmp)/'state'), patch.object(ops,'DAILY',Path(tmp)/'daily'):
                calls=[]
                def command(args, **kwargs):
                    calls.append(args)
                    if 'ps' in args: return Mock(stdout=b'api\nworker\nchat-worker\n')
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

    def test_no_credentials_no_outbound_alert(self):
        with patch.dict(ops.os.environ,{},clear=True), patch.object(ops,'http_open') as request:
            self.assertFalse(ops.notify('test'));request.assert_not_called()

    def test_provider_rejection_is_not_success(self):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=b'{"errcode":310000}'
        with patch.dict(ops.os.environ,{'OPS_DINGTALK_TOKEN':'test','OPS_DINGTALK_SECRET':'test'}),patch.object(ops,'http_open',return_value=response):
            self.assertFalse(ops.notify('test'))

if __name__=='__main__':unittest.main()
