import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tarfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from prepare_production_env import normalize_host, render_env, write_new
from certificates import Certificates, CertError, certificate_arguments, exclusive_lock, load_config
from package_release import create_package

OPENSSL = next((str(p) for p in [Path('/opt/homebrew/opt/openssl@3/bin/openssl'), Path('/usr/local/opt/openssl@3/bin/openssl')] if p.exists()), shutil.which('openssl'))


class EnvironmentTests(unittest.TestCase):
    def test_release_excludes_secrets_runtime_data_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = {'.env': 'PRIVATE_PAYLOAD_42', '.env.production': 'PRIVATE_PAYLOAD_42', '.env.example': 'DJANGO_SECRET_KEY=', '.local-credentials.txt': 'PRIVATE_PAYLOAD_42', 'backend/desk/views.py': '# application', 'backend/private-media/secret.py': 'PRIVATE_PAYLOAD_42', 'backend/dev.sqlite3': 'PRIVATE_PAYLOAD_42', 'deploy/certs/private.key': 'PRIVATE_PAYLOAD_42', 'frontend/src/App.vue': '<template/>', 'frontend/public/assets/secret.pem': 'PRIVATE_PAYLOAD_42'}
            for name, data in fixtures.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(data)
            (root / 'frontend/src/leak.ts').symlink_to(root / '.env')
            output = root / 'release/test.tar.gz'
            create_package(root, output)
            with tarfile.open(output) as archive:
                names = archive.getnames()
                self.assertIn('sapdesk/.env.example', names)
                self.assertIn('sapdesk/frontend/src/App.vue', names)
                for member in archive.getmembers():
                    self.assertTrue(member.isfile())
                    self.assertEqual(member.uid, 0)
                    self.assertNotIn(b'PRIVATE_PAYLOAD_42', archive.extractfile(member).read())

    def test_public_ipv4_and_domain(self):
        self.assertEqual(normalize_host('43.130.36.105'), '43.130.36.105')
        self.assertEqual(normalize_host('Desk.Example.com'), 'desk.example.com')
        for host in ['127.0.0.1', '192.168.1.2', '10.0.0.1', '0.0.0.0', '224.1.1.1', '192.0.2.1', '999.1.1.1', 'https://43.130.36.105', '43.130.36.105:443', 'a..com', '-a.com', 'localhost', '$(echo bad).com']:
            with self.subTest(host=host), self.assertRaises(ValueError):
                normalize_host(host)

    def test_environment_preserves_https_and_never_overwrites(self):
        template = (ROOT / '.env.example').read_text()
        first = render_env(template, '43.130.36.105', 'admin@example.com')
        second = render_env(template, '43.130.36.105', 'admin@example.com')
        self.assertNotEqual(first, second)
        self.assertIn('DJANGO_DEBUG=false', first)
        self.assertIn('CSRF_TRUSTED_ORIGINS=https://43.130.36.105', first)
        self.assertIn('DINGTALK_ENABLED=false', first)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env.production'
            write_new(path, first)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                write_new(path, second)
            self.assertEqual(path.read_text(), first)
            config = load_config(path)
            self.assertEqual(config['APP_DOMAIN'], '43.130.36.105')
            self.assertNotIn('DJANGO_SECRET_KEY', config)

    def test_ip_requires_shortlived_profile(self):
        self.assertEqual(certificate_arguments('43.130.36.105'), ['--ip-address', '43.130.36.105', '--required-profile', 'shortlived'])
        self.assertEqual(certificate_arguments('desk.example.com'), ['-d', 'desk.example.com'])

    def test_lock_prevents_concurrent_issuance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cert.lock'
            with exclusive_lock(path):
                with self.assertRaises(CertError):
                    with exclusive_lock(path):
                        pass


@unittest.skipUnless(OPENSSL, 'OpenSSL is required for certificate validation tests')
class CertificateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.cert_dir = Path(cls.fixtures.name)
        def run(*args):
            subprocess.run([OPENSSL, *args], cwd=cls.cert_dir, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        cls.generate = staticmethod(run)
        run('req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes', '-keyout', 'ca.key', '-out', 'ca.pem', '-days', '10', '-subj', '/CN=SAPDeskTestCA', '-addext', 'basicConstraints=critical,CA:TRUE')
        run('req', '-new', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes', '-keyout', 'leaf.key', '-out', 'leaf.csr', '-subj', '/CN=SAPDeskTest')
        (cls.cert_dir / 'extensions').write_text('subjectAltName=IP:43.130.36.105\nbasicConstraints=critical,CA:FALSE\nkeyUsage=digitalSignature\nextendedKeyUsage=serverAuth\n')
        run('x509', '-req', '-in', 'leaf.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key', '-CAcreateserial', '-days', '6', '-extfile', 'extensions', '-out', 'leaf.pem')
        (cls.cert_dir / 'fullchain.pem').write_bytes((cls.cert_dir / 'leaf.pem').read_bytes() + (cls.cert_dir / 'ca.pem').read_bytes())
        (cls.cert_dir / 'empty-ca-dir').mkdir()

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        write_new(self.root / '.env.production', render_env((ROOT / '.env.example').read_text(), '43.130.36.105', 'admin@example.com'))
        self.environ = patch.dict(os.environ, {'OPENSSL_BIN': OPENSSL, 'SSL_CERT_FILE': str(self.cert_dir / 'ca.pem'), 'SSL_CERT_DIR': str(self.cert_dir / 'empty-ca-dir')})
        self.environ.start()
        self.addCleanup(self.environ.stop)
        self.manager = Certificates(self.root)
        self.manager.prepare_directories()
        self.source = self.root / 'deploy/letsencrypt/live/sapdesk'
        self.source.mkdir(parents=True)
        shutil.copyfile(self.cert_dir / 'fullchain.pem', self.source / 'fullchain.pem')
        shutil.copyfile(self.cert_dir / 'leaf.key', self.source / 'privkey.pem')

    def test_real_openssl_accepts_matching_ip_and_key(self):
        self.assertTrue(self.manager.validate(self.source))

    def test_wrong_ip_is_rejected(self):
        self.manager.host = '8.8.8.8'
        with self.assertRaises(CertError):
            self.manager.validate(self.source)

    def test_wrong_key_is_rejected(self):
        shutil.copyfile(self.cert_dir / 'ca.key', self.source / 'privkey.pem')
        with self.assertRaises(CertError):
            self.manager.validate(self.source)

    def test_expired_certificate_is_rejected(self):
        self.generate('x509', '-req', '-in', 'leaf.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key', '-CAcreateserial', '-days', '0', '-extfile', 'extensions', '-out', 'expired.pem')
        (self.source / 'fullchain.pem').write_bytes((self.cert_dir / 'expired.pem').read_bytes() + (self.cert_dir / 'ca.pem').read_bytes())
        with self.assertRaises(CertError):
            self.manager.validate(self.source)

    def test_untrusted_certificate_is_never_activated(self):
        with patch.dict(os.environ, {'SSL_CERT_FILE': '/nonexistent/sapdesk-test-roots'}):
            with self.assertRaises(CertError):
                self.manager.activate(False)
        self.assertFalse((self.root / 'deploy/certs/current').exists())

    def test_atomic_activation_and_private_key_permissions(self):
        self.manager.compose = Mock()
        self.manager.live_certificate = Mock()
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.activate(True)
        current = self.root / 'deploy/certs/current'
        self.assertTrue(current.is_symlink())
        self.assertTrue(current.resolve().is_relative_to(self.root / 'deploy/certs/releases'))
        self.assertEqual((current / 'privkey.pem').stat().st_mode & 0o777, 0o600)
        self.manager.compose.assert_any_call('exec', '-T', 'web', 'nginx', '-t')
        self.manager.compose.assert_any_call('exec', '-T', 'web', 'nginx', '-s', 'reload')
        self.manager.live_certificate.assert_called_once()

    def test_failed_reload_restores_previous_link(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.activate(False)
        current = self.root / 'deploy/certs/current'
        previous = os.readlink(current)
        # A harmless extra PEM newline gives a distinct complete certificate release.
        with (self.source / 'fullchain.pem').open('ab') as stream:
            stream.write(b'\n')
        self.manager.compose = Mock(side_effect=[RuntimeError('nginx validation failed'), None, None])
        with self.assertRaises(RuntimeError):
            self.manager.activate(True)
        self.assertEqual(os.readlink(current), previous)

    def test_staging_never_activates_or_uses_production_store(self):
        self.manager.web_running = Mock(return_value=False)
        self.manager.probe_http = Mock()
        self.manager.compose = Mock()
        self.manager.activate = Mock()
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.issue(staging=True, agree_tos=True)
        self.manager.activate.assert_not_called()
        issuing = [c for c in self.manager.compose.call_args_list if 'certonly' in c.args][0]
        self.assertIn('--staging', issuing.args)
        self.assertTrue(issuing.kwargs['staging'])
        self.manager.compose.assert_any_call('stop', 'challenge', certificates=True)

    def test_initial_issue_failure_still_stops_only_bootstrap_server(self):
        self.manager.web_running = Mock(return_value=False)
        self.manager.probe_http = Mock(side_effect=CertError('probe failed'))
        self.manager.compose = Mock()
        with self.assertRaises(CertError):
            self.manager.issue(staging=False, agree_tos=True)
        self.manager.compose.assert_any_call('stop', 'challenge', certificates=True)
        self.assertFalse(any('web' in call.args for call in self.manager.compose.call_args_list))

    def test_renewal_dry_run_does_not_activate(self):
        self.manager.web_running = Mock(return_value=True)
        self.manager.probe_http = Mock()
        self.manager.compose = Mock()
        self.manager.activate = Mock()
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.renew(dry_run=True)
        self.manager.activate.assert_not_called()
        self.assertIn('--dry-run', self.manager.compose.call_args.args)

    def test_renewal_retries_activation_even_if_certbot_did_not_issue(self):
        self.manager.web_running = Mock(return_value=True)
        self.manager.probe_http = Mock()
        self.manager.compose = Mock()
        self.manager.activate = Mock()
        self.manager.renew(dry_run=False)
        self.manager.activate.assert_called_once_with(True)


if __name__ == '__main__':
    unittest.main()
