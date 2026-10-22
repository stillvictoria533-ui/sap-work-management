"""Mac-only, model-free scan followed by an on-demand Codex worker.

Defaults to shadow mode. Production mode refuses to run while the existing
sap-ai heartbeat is active. Logs contain status codes only, never ticket text.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import tomllib

ROOT = Path(__file__).resolve().parent.parent
SCAN_SCRIPT = ROOT / 'scripts/ai_ticket_scan.cjs'
PROMPT_FILE = ROOT / 'docs/AI_TICKET_ON_DEMAND_PROMPT.md'
STATE_FILE = ROOT / 'release/ai-ticket-dispatch-state.json'
LOCK_FILE = ROOT / 'release/.ai-ticket-dispatch.lock'
HEARTBEAT_FILE = Path.home() / '.codex/automations/sap-ai/automation.toml'
RUN_FLAG = ROOT / 'release/ai-ticket-dispatch-enabled'
WORKER_TIMEOUT_SECONDS = 50 * 60
FAILURE_COOLDOWN_SECONDS = 15 * 60
WORKER_MODEL = 'gpt-6.1-sol'
WORKER_CODEX_BIN = Path.home() / '.codex/runtime/codex-cli-0.162.1/node_modules/.bin/codex'
TENCENT_MCP_PYTHON = '/usr/local/bin/python3'
TENCENT_MCP_SCRIPT = str(Path.home() / '.codex/mcp/tencent-lighthouse/run.py')


def save_state(**changes):
    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except (ValueError, OSError):
            pass
    state.update(changes)
    temp = STATE_FILE.with_suffix('.json.tmp')
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(state, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    temp.replace(STATE_FILE)
    return state


def heartbeat_paused():
    try:
        return tomllib.loads(HEARTBEAT_FILE.read_text()).get('status') == 'PAUSED'
    except (OSError, ValueError):
        return False


def scan_queue(node_binary):
    result = subprocess.run(
        [node_binary, str(SCAN_SCRIPT)], cwd=ROOT, capture_output=True,
        text=True, timeout=90, check=False,
    )
    status = result.stdout.strip()
    if result.returncode or (status not in {'EMPTY', 'DISABLED'} and
                             not re.fullmatch(r'READY:[0-9a-f]{16}', status)):
        raise RuntimeError('SCAN_FAILED')
    return status


def launch_worker(codex_binary):
    prompt = PROMPT_FILE.read_text()
    args = [
        codex_binary, 'exec', '--ignore-user-config',
        '-c', f'mcp_servers.tencent-lighthouse.command="{TENCENT_MCP_PYTHON}"',
        '-c', f'mcp_servers.tencent-lighthouse.args=["{TENCENT_MCP_SCRIPT}"]',
        '-c', 'mcp_servers.tencent-lighthouse.startup_timeout_sec=30',
        '-c', 'mcp_servers.tencent-lighthouse.tool_timeout_sec=120',
        '-m', WORKER_MODEL,
        '-c', 'model_reasoning_effort="high"',
        '--approve-for-me',
        '--skip-git-repo-check', '-C', str(ROOT), '-',
    ]
    process = subprocess.Popen(
        args, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, text=True, start_new_session=True,
    )
    def stop_child():
        if process.poll() is not None:
            return
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()

    def interrupted(signum, _frame):
        stop_child()
        raise SystemExit(128 + signum)

    old_term = signal.signal(signal.SIGTERM, interrupted)
    old_int = signal.signal(signal.SIGINT, interrupted)
    try:
        process.communicate(prompt, timeout=WORKER_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        stop_child()
        return 'TIMEOUT'
    finally:
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
    return 'OK' if process.returncode == 0 else 'WORKER_FAILED'


def run_once(mode, node_binary=None, codex_binary=None):
    node_binary = node_binary or shutil.which('node')
    codex_binary = codex_binary or str(WORKER_CODEX_BIN)
    if not node_binary:
        raise RuntimeError('NODE_MISSING')
    if mode == 'run' and not heartbeat_paused():
        raise RuntimeError('HEARTBEAT_STILL_ACTIVE')
    if mode == 'run' and not RUN_FLAG.exists():
        raise RuntimeError('NOT_PUBLISHED')
    scan_result = scan_queue(node_binary)
    status = scan_result.split(':', 1)[0]
    now = time.time()
    save_state(last_scan_at=now, last_scan=status, last_error=None)
    if status != 'READY' or mode == 'shadow':
        return 'SHADOW_' + status if mode == 'shadow' else status
    if not heartbeat_paused():
        raise RuntimeError('HEARTBEAT_STILL_ACTIVE')
    if not os.access(codex_binary, os.X_OK):
        raise RuntimeError('CODEX_MISSING')
    state = json.loads(STATE_FILE.read_text())
    if (state.get('last_worker_result') not in (None, 'OK') and
            now - state.get('last_worker_at', 0) < FAILURE_COOLDOWN_SECONDS):
        return 'WORKER_COOLDOWN'
    save_state(last_worker_at=now, last_worker_result='RUNNING')
    result = launch_worker(codex_binary)
    if result == 'OK':
        try:
            if scan_queue(node_binary) == scan_result:
                result = 'NO_PROGRESS'
        except RuntimeError:
            result = 'POST_SCAN_FAILED'
    save_state(last_worker_finished_at=time.time(), last_worker_result=result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shadow', action='store_true', help='Scan only; never start Codex (default).')
    parser.add_argument('--run', action='store_true', help='Start Sol only when ready and the old heartbeat is paused.')
    args = parser.parse_args()
    if args.shadow and args.run:
        parser.error('Choose at most one mode.')
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, 'r+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('ALREADY_RUNNING')
            return 0
        try:
            result = run_once('run' if args.run else 'shadow')
            print(result)
            return 1 if result in {'WORKER_FAILED', 'TIMEOUT', 'NO_PROGRESS', 'POST_SCAN_FAILED'} else 0
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            allowed = {'NODE_MISSING', 'SCAN_FAILED', 'HEARTBEAT_STILL_ACTIVE', 'NOT_PUBLISHED', 'CODEX_MISSING'}
            reason = str(error) if str(error) in allowed else 'DISPATCH_FAILED'
            save_state(last_error_at=time.time(), last_error=reason)
            print('DISPATCH_FAILED', file=sys.stderr)
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
