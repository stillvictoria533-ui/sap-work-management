import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restore_drill as drill


class RestoreDrillTests(unittest.TestCase):
    def fixture(self, directory, member='1/file', kind='file'):
        directory = Path(directory)
        (directory/'database.dump').write_bytes(b'PGDMP synthetic fixture')
        with tarfile.open(directory/'attachments.tar.gz', 'w:gz') as archive:
            info = tarfile.TarInfo(member)
            if kind == 'symlink':
                info.type = tarfile.SYMTYPE; info.linkname = '/etc/passwd'; archive.addfile(info)
            else:
                data = b'example attachment'; info.size = len(data); archive.addfile(info, io.BytesIO(data))
        (directory/'configuration.tar.gz').write_bytes(b'private fixture')
        sums = {name: hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in drill.FILES}
        (directory/'SHA256SUMS').write_text(''.join(f'{value}  {name}\n' for name, value in sums.items()))
        (directory/'manifest.json').write_text(json.dumps({'format': 'sapdesk-daily-v1',
            'completed_at': time.time(), 'sha256': sums,
            'counts': {'tickets': 1, 'attachments': 1, 'attachment_bytes': 18}}))
        return directory

    def test_valid_and_legacy_backups_are_verified_without_extracting_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            result = drill.verify_backup(root)
            self.assertEqual(result['archive_files'], 1)
            self.assertEqual(result['expected_counts']['tickets'], 1)
            manifest = json.loads((root/'manifest.json').read_text()); manifest.pop('counts')
            (root/'manifest.json').write_text(json.dumps(manifest))
            self.assertIsNone(drill.verify_backup(root)['expected_counts'])

    def test_corruption_and_incomplete_backups_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            (root/'attachments.tar.gz').write_bytes(b'tampered')
            with self.assertRaisesRegex(drill.DrillError, 'backup_checksum_mismatch'):
                drill.verify_backup(root)
            incomplete = root/'snapshot.incomplete'; incomplete.mkdir()
            with self.assertRaisesRegex(drill.DrillError, 'backup_directory_invalid'):
                drill.verify_backup(incomplete)

    def test_invalid_manifest_timestamp_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            manifest = json.loads((root/'manifest.json').read_text()); manifest['completed_at'] = 10**1000
            (root/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(drill.DrillError, 'manifest_invalid'):
                drill.verify_backup(root)

    def test_archive_traversal_and_symlinks_are_rejected_even_with_valid_checksums(self):
        for name, kind in [('../outside', 'file'), ('/absolute', 'file'), ('1/link', 'symlink')]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                with self.assertRaisesRegex(drill.DrillError, 'attachment_archive_unsafe'):
                    drill.verify_backup(self.fixture(folder, name, kind))

    def test_cleanup_never_deletes_non_drill_or_wrong_label_resources(self):
        with patch.object(drill, 'docker') as docker:
            with self.assertRaisesRegex(drill.DrillError, 'cleanup_name_invalid'):
                drill.cleanup_resource('volume', 'sapdesk_database', 'a'*16)
            docker.assert_not_called()
        with patch.object(drill, 'docker', return_value=Mock(returncode=0, stdout=b'other-owner')) as docker:
            with self.assertRaisesRegex(drill.DrillError, 'cleanup_identity_mismatch'):
                drill.cleanup_resource('volume', 'sapdesk-drill-'+'a'*16+'-pg', 'a'*16)
            self.assertEqual(docker.call_count, 1)

    def test_invalid_image_cannot_be_interpreted_as_a_docker_flag(self):
        with patch.object(drill, 'docker') as docker:
            with self.assertRaisesRegex(drill.DrillError, 'image_name_invalid'):
                drill.restore_drill('/unused', {'expected_counts': None}, '--privileged')
            docker.assert_not_called()


if __name__ == '__main__':
    unittest.main()
