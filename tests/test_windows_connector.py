"""Static packaging/boundary tests, NOT a substitute for Windows COM execution."""
import re
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WindowsConnectorBoundaries(unittest.TestCase):
    def test_reader_has_no_ui_mutation_or_arbitrary_screen_extraction(self):
        text = (ROOT / 'connector/windows/Read-Ds4Gui.ps1').read_text()
        for method in ['SendVKey', 'StartTransaction', 'EndTransaction', 'OpenConnection', 'FindById',
                       'GetObjectTree', 'SendCommand', 'SetFocus', 'Press(', 'Invoke-Expression']:
            self.assertNotIn(method.lower(), text.lower())
        self.assertIn("BindToMoniker('SAPGUI')", text)
        self.assertIn("SystemName -ceq 'DS4'", text)
        self.assertIn("Client -ceq '110'", text)
        self.assertIn("'10.2.22.34'", text)
        self.assertNotIn('Invoke-RestMethod', text)

    def test_launcher_respects_execution_policy_and_uses_builtin_powershell(self):
        for path in (ROOT / 'connector/windows').glob('*.cmd'):
            text = path.read_text().lower()
            self.assertIn('windowspowershell', text)
            self.assertNotIn('-executionpolicy', text)
            self.assertNotIn('bypass', text)
            self.assertNotIn('node', text)
        all_code = '\n'.join(p.read_text() for p in (ROOT/'connector/windows').glob('*.ps1'))
        for token in ['Set-ExecutionPolicy', 'ServerCertificateValidationCallback', 'Invoke-Expression',
                      'New-Service', 'Set-ItemProperty', 'Start-Transaction']:
            self.assertNotIn(token.lower(), all_code.lower())

    def test_network_credentials_redirect_and_gui_timeout_boundaries(self):
        text = (ROOT / 'connector/windows/Connector.Core.ps1').read_text()
        self.assertIn('$request.AllowAutoRedirect = $false', text)
        self.assertIn("$Job.operation -cne 'gui_session'", text)
        self.assertIn("$Job.object_name -cne 'CURRENT'", text)
        self.assertIn('.AddSeconds(60)', text)
        self.assertIn('$process.Kill()', text)
        self.assertIn('if (-not $Offline) { Invoke-SapDeskPulse }', text)
