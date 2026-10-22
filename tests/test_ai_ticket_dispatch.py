import contextlib
import fcntl
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import subprocess


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/ai_ticket_dispatch.py'
SPEC = importlib.util.spec_from_file_location('ai_ticket_dispatch', SCRIPT)
dispatch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dispatch)


class DispatchTests(unittest.TestCase):
    def test_empty_queue_never_starts_a_model(self):
        with patch.object(dispatch, 'heartbeat_paused', return_value=True), \
             patch.object(dispatch, 'RUN_FLAG', SCRIPT), \
             patch.object(dispatch, 'scan_queue', return_value='EMPTY'), \
             patch.object(dispatch, 'save_state'), \
             patch.object(dispatch, 'launch_worker') as worker:
            self.assertEqual(dispatch.run_once('run', '/usr/bin/true', '/usr/bin/true'), 'EMPTY')
            worker.assert_not_called()

    def test_shadow_scan_never_starts_a_model(self):
        with patch.object(dispatch, 'scan_queue', return_value='READY:0123456789abcdef'), \
             patch.object(dispatch, 'save_state'), \
             patch.object(dispatch, 'launch_worker') as worker:
            self.assertEqual(dispatch.run_once('shadow', '/usr/bin/true', '/usr/bin/true'), 'SHADOW_READY')
            worker.assert_not_called()

    def test_old_heartbeat_blocks_production_before_scanning(self):
        with patch.object(dispatch, 'heartbeat_paused', return_value=False), \
             patch.object(dispatch, 'scan_queue') as scanner:
            with self.assertRaisesRegex(RuntimeError, 'HEARTBEAT_STILL_ACTIVE'):
                dispatch.run_once('run', '/usr/bin/true', '/usr/bin/true')
            scanner.assert_not_called()

    def test_ready_queue_starts_one_worker_and_failure_cools_down(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(dispatch, 'STATE_FILE', Path(folder) / 'state.json'), \
             patch.object(dispatch, 'RUN_FLAG', SCRIPT), \
             patch.object(dispatch, 'heartbeat_paused', return_value=True), \
             patch.object(dispatch, 'scan_queue', return_value='READY:0123456789abcdef'), \
             patch.object(dispatch, 'launch_worker', return_value='WORKER_FAILED') as worker:
            self.assertEqual(dispatch.run_once('run', '/usr/bin/true', '/usr/bin/true'), 'WORKER_FAILED')
            self.assertEqual(dispatch.run_once('run', '/usr/bin/true', '/usr/bin/true'), 'WORKER_COOLDOWN')
            worker.assert_called_once()

    def test_no_progress_after_sol_exit_enters_cooldown(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(dispatch, 'STATE_FILE', Path(folder) / 'state.json'), \
             patch.object(dispatch, 'RUN_FLAG', SCRIPT), \
             patch.object(dispatch, 'heartbeat_paused', return_value=True), \
             patch.object(dispatch, 'scan_queue', return_value='READY:0123456789abcdef'), \
             patch.object(dispatch, 'launch_worker', return_value='OK') as worker:
            self.assertEqual(dispatch.run_once('run', '/usr/bin/true', '/usr/bin/true'), 'NO_PROGRESS')
            self.assertEqual(dispatch.run_once('run', '/usr/bin/true', '/usr/bin/true'), 'WORKER_COOLDOWN')
            worker.assert_called_once()

    def test_unpublished_run_cannot_scan_or_start_a_worker(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(dispatch, 'RUN_FLAG', Path(folder) / 'missing'), \
             patch.object(dispatch, 'heartbeat_paused', return_value=True), \
             patch.object(dispatch, 'scan_queue') as scanner:
            with self.assertRaisesRegex(RuntimeError, 'NOT_PUBLISHED'):
                dispatch.run_once('run', '/usr/bin/true', '/usr/bin/true')
            scanner.assert_not_called()

    def test_existing_lock_skips_a_second_dispatch(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(dispatch, 'LOCK_FILE', Path(folder) / 'lock'), \
             patch.object(sys, 'argv', ['ai_ticket_dispatch.py', '--shadow']), \
             patch.object(dispatch, 'run_once') as run:
            with open(dispatch.LOCK_FILE, 'w+') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(dispatch.main(), 0)
            self.assertEqual(output.getvalue().strip(), 'ALREADY_RUNNING')
            run.assert_not_called()

    def test_worker_uses_scoped_mcp_and_supported_sol_model(self):
        class FakeProcess:
            returncode = 0
            def communicate(self, prompt, timeout):
                self.prompt = prompt
                self.timeout = timeout
            def poll(self):
                return self.returncode

        with tempfile.TemporaryDirectory() as folder, \
             patch.object(dispatch, 'PROMPT_FILE', Path(folder) / 'prompt.md'), \
             patch.object(dispatch.subprocess, 'Popen', return_value=FakeProcess()) as spawn:
            dispatch.PROMPT_FILE.write_text('test prompt')
            self.assertEqual(dispatch.launch_worker('/path/to/codex'), 'OK')
            args = spawn.call_args.args[0]
            self.assertEqual(args[args.index('-m') + 1], 'gpt-6.1-sol')
            self.assertIn('--ignore-user-config', args)
            self.assertIn('--approve-for-me', args)
            self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', args)
            self.assertEqual(sum('mcp_servers.' in item for item in args), 4)

    def test_worker_timeout_stops_its_process_group(self):
        class SlowProcess:
            pid = 12345
            returncode = None
            def communicate(self, _prompt, timeout):
                raise subprocess.TimeoutExpired('codex', timeout)
            def poll(self):
                return self.returncode
            def wait(self, timeout=None):
                self.returncode = 143

        with tempfile.TemporaryDirectory() as folder, \
             patch.object(dispatch, 'PROMPT_FILE', Path(folder) / 'prompt.md'), \
             patch.object(dispatch.subprocess, 'Popen', return_value=SlowProcess()), \
             patch.object(dispatch.os, 'killpg') as stop:
            dispatch.PROMPT_FILE.write_text('test prompt')
            self.assertEqual(dispatch.launch_worker('/path/to/codex'), 'TIMEOUT')
            stop.assert_called_once_with(12345, dispatch.signal.SIGTERM)


if __name__ == '__main__':
    unittest.main()
