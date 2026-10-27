"""Text/JSON-only Codex planner. No execution, file, network or MCP tools."""
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import tempfile
import time

PINNED_VERSION = 'codex-cli 0.162.1'
MODEL = 'gpt-6.1-sol'
DISABLED_FEATURES = (
    'shell_tool', 'unified_exec', 'apps', 'plugins', 'hooks', 'browser_use',
    'browser_use_external', 'browser_use_full_cdp_access', 'computer_use',
    'code_mode', 'code_mode_host', 'view_image', 'image_generation', 'multi_agent',
    'multi_agent_v2', 'memories', 'skill_search', 'skill_mcp_dependency_install',
    'tool_suggest', 'workspace_dependencies', 'goals', 'sleep_tool',
    'request_permissions_tool', 'remote_plugin', 'in_app_browser', 'artifact',
    'agent_message_board', 'auth_elicitation', 'tool_call_mcp_elicitation',
)
ACTIVE_CHILDREN = set()

PLAN_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'action': {'type': 'string', 'enum': ['query', 'reply', 'clarification', 'defer']},
        'plan': {'anyOf': [{'type': 'null'}, {'type': 'object', 'additionalProperties': False,
            'properties': {
                'command': {'type': 'string', 'enum': ['ddic', 'source', 'data', 'tcode']},
                'object': {'type': 'string'}, 'kind': {'type': 'string'}, 'method': {'type': 'string'},
                'fields': {'type': 'array', 'items': {'type': 'string'}},
                'filters': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                    'properties': {'field': {'type': 'string'}, 'value': {'type': 'string'}}, 'required': ['field', 'value']}},
                'rows': {'type': 'integer'},
            }, 'required': ['command', 'object', 'kind', 'method', 'fields', 'filters', 'rows']}]},
        'body': {'type': 'string'},
        'evidence_ids': {'type': 'array', 'items': {'type': 'string'}},
        'reason': {'type': 'string'},
    }, 'required': ['action', 'plan', 'body', 'evidence_ids', 'reason'],
}


class WorkerError(Exception):
    def __init__(self, code='runtime_error', detail_code=''):
        self.code = code
        self.detail_code = detail_code
        super().__init__(code)


def model_environment():
    names = ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'CODEX_HOME',
        'HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'NO_PROXY',
        'https_proxy', 'http_proxy', 'all_proxy', 'no_proxy', 'SSL_CERT_FILE', 'SSL_CERT_DIR')
    return {key: os.environ[key] for key in names if key in os.environ}


def arguments(binary, directory, instructions, schema_path):
    args = [str(binary), 'exec', '--ignore-user-config', '--strict-config', '--ephemeral',
        '--skip-git-repo-check', '--sandbox', 'read-only', '--color', 'never', '--json',
        '-C', str(directory), '--model', MODEL, '--output-schema', str(schema_path)]
    values = {
        'approval_policy': 'never', 'web_search': 'disabled', 'project_doc_max_bytes': 0,
        'developer_instructions': instructions, 'mcp_servers': {}, 'agents.enabled': False,
        'shell_environment_policy.inherit': 'none', 'suppress_unstable_features_warning': True,
        'features.skip_host_skill_discovery': True, 'skills.bundled.enabled': False,
        'model_reasoning_effort': 'high',
    }
    for key, value in values.items():
        args += ['-c', key + '=' + json.dumps(value, ensure_ascii=False)]
    for feature in DISABLED_FEATURES:
        args += ['--disable', feature]
    return args + ['-']


def stop_child(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait(timeout=3)


def stop_children():
    for process in list(ACTIVE_CHILDREN):
        stop_child(process)


def run_bounded(args, *, input_text='', timeout=60, environment=None, cwd=None):
    """Trusted fixed adapters only. Kill descendants on timeout and cancellation."""
    process = subprocess.Popen(args, cwd=cwd, env=environment, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    ACTIVE_CHILDREN.add(process)
    try:
        output, _ = process.communicate(input_text, timeout=timeout)
        if process.returncode or len(output.encode()) > 1_000_000:
            raise WorkerError()
        return output
    except subprocess.TimeoutExpired:
        raise WorkerError('time_limit') from None
    finally:
        stop_child(process)
        ACTIVE_CHILDREN.discard(process)


class CodexPlanner:
    def __init__(self, binary, instructions):
        self.binary, self.instructions = str(binary), instructions
        self.checked = False

    def __call__(self, context, pulse):
        prompt = json.dumps(context, ensure_ascii=False, allow_nan=False)
        if len(prompt.encode()) > 256 * 1024: raise WorkerError('context_incomplete')
        with tempfile.TemporaryDirectory(prefix='sapdesk-ticket-model-') as directory:
            if not self.checked:
                version = run_bounded([self.binary, '--version'], timeout=5,
                    environment=model_environment(), cwd=directory)
                if version.strip() != PINNED_VERSION: raise WorkerError('authorization_missing', 'cli_version_mismatch')
                self.checked = True
            schema = Path(directory) / 'response.schema.json'
            schema.write_text(json.dumps(PLAN_SCHEMA))
            process = subprocess.Popen(arguments(self.binary, directory, self.instructions, schema),
                cwd=directory, env=model_environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, start_new_session=True)
            ACTIVE_CHILDREN.add(process)
            selector = selectors.DefaultSelector()
            started, size, buffer, completed, answer = time.monotonic(), 0, b'', False, None
            try:
                pending_input = memoryview(prompt.encode())
                os.set_blocking(process.stdin.fileno(), False)
                os.set_blocking(process.stdout.fileno(), False)
                selector.register(process.stdout, selectors.EVENT_READ)
                selector.register(process.stdin, selectors.EVENT_WRITE)
                while True:
                    pulse()
                    if time.monotonic() - started > 300: raise WorkerError('time_limit')
                    for key, _ in selector.select(timeout=0.25):
                        if key.fileobj is process.stdin:
                            written = os.write(key.fd, pending_input[:65536])
                            pending_input = pending_input[written:]
                            if not pending_input:
                                selector.unregister(process.stdin)
                                process.stdin.close()
                            continue
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        size += len(chunk)
                        if size > 512 * 1024: raise WorkerError('context_incomplete')
                        buffer += chunk
                        while b'\n' in buffer:
                            line, buffer = buffer.split(b'\n', 1)
                            if not line.strip(): continue
                            event = json.loads(line)
                            item = event.get('item', {})
                            if item.get('type') == 'error' and item.get('message', '').startswith(
                                    'Code Mode is unavailable because code-mode host is disabled.'):
                                # Pinned CLI emits this startup diagnostic even for
                                # text-only requests. No code-mode host is enabled.
                                continue
                            # Capabilities are disabled before generation. This check
                            # additionally rejects any unexpected execution event.
                            if item and item.get('type') not in {'agent_message', 'reasoning'}:
                                raise WorkerError('authorization_missing', 'unexpected_model_event')
                            if event.get('type') == 'item.completed' and item.get('type') == 'agent_message':
                                answer = item.get('text')
                            if event.get('type') == 'turn.completed': completed = True
                            if event.get('type') in {'error', 'turn.failed'}: raise WorkerError()
                    if process.poll() is not None and not selector.get_map(): break
                if process.returncode or not completed or buffer.strip() or not isinstance(answer, str): raise WorkerError()
                return json.loads(answer)
            except (ValueError, TypeError, OSError):
                raise WorkerError() from None
            finally:
                selector.close()
                stop_child(process)
                ACTIVE_CHILDREN.discard(process)
                process.stdout.close()
                if not process.stdin.closed: process.stdin.close()
