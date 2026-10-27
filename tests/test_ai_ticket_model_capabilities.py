"""Optional real CLI / loopback-only capture; no model API or real credentials."""
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from ai_ticket_model import arguments, PLAN_SCHEMA, CodexPlanner


class RealCLICapabilityTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('AI_TICKET_CODEX_TEST_BIN'), 'set AI_TICKET_CODEX_TEST_BIN for the installed CLI capture')
    def test_real_planner_completes_using_only_a_loopback_fixture(self):
        captured = []
        decision = {'action': 'clarification', 'plan': None, 'body': '请补充必要的对象信息。', 'evidence_ids': [], 'reason': ''}
        text = json.dumps(decision, ensure_ascii=False)
        message = {'id': 'msg_local', 'type': 'message', 'role': 'assistant', 'status': 'completed',
            'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}
        response = {'id': 'resp_local', 'object': 'response', 'status': 'completed', 'model': 'gpt-6.1-sol',
            'output': [message], 'usage': {'input_tokens': 10, 'output_tokens': 20, 'total_tokens': 30}}
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                captured.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                events = [
                    {'type': 'response.created', 'response': {**response, 'status': 'in_progress', 'output': []}},
                    {'type': 'response.output_item.added', 'output_index': 0, 'item': {**message, 'status': 'in_progress', 'content': []}},
                    {'type': 'response.content_part.added', 'item_id': 'msg_local', 'output_index': 0, 'content_index': 0,
                        'part': {'type': 'output_text', 'text': '', 'annotations': []}},
                    {'type': 'response.output_text.delta', 'item_id': 'msg_local', 'output_index': 0, 'content_index': 0, 'delta': text},
                    {'type': 'response.output_text.done', 'item_id': 'msg_local', 'output_index': 0, 'content_index': 0, 'text': text},
                    {'type': 'response.output_item.done', 'output_index': 0, 'item': message},
                    {'type': 'response.completed', 'response': response},
                ]
                for index, event in enumerate(events):
                    event['sequence_number'] = index
                    self.wfile.write(('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n').encode())
                self.wfile.flush()
        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix='ticket-planner-loopback-') as folder:
                directory = Path(folder)
                (directory / 'codex-home').mkdir()
                environment = {'PATH': os.environ['PATH'], 'HOME': folder, 'CODEX_HOME': str(directory / 'codex-home'),
                    'NO_PROXY': 'localhost,127.0.0.1,::1', 'no_proxy': 'localhost,127.0.0.1,::1'}
                def local_arguments(*args):
                    result = arguments(*args)[:-1]
                    values = {'model_provider': 'localtest', 'model_providers.localtest.name': 'loopback',
                        'model_providers.localtest.base_url': f'http://127.0.0.1:{server.server_port}/v1',
                        'model_providers.localtest.wire_api': 'responses', 'model_providers.localtest.requires_openai_auth': False,
                        'model_providers.localtest.request_max_retries': 0, 'model_providers.localtest.stream_max_retries': 0,
                        'features.system_proxy_fallback': False, 'features.respect_system_proxy': False,
                        'features.enable_request_compression': False}
                    for key, value in values.items(): result += ['-c', key + '=' + json.dumps(value)]
                    return result + ['-']
                planner = CodexPlanner(os.environ['AI_TICKET_CODEX_TEST_BIN'], 'Return JSON only.')
                with patch('ai_ticket_model.arguments', local_arguments), patch('ai_ticket_model.model_environment', return_value=environment):
                    self.assertEqual(planner({'synthetic_local_fixture': True}, lambda: None), decision)
                self.assertTrue(captured)
                self.assertTrue(all(not request.get('tools') for request in captured))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    @unittest.skipUnless(os.environ.get('AI_TICKET_CODEX_TEST_BIN'), 'set AI_TICKET_CODEX_TEST_BIN for the installed CLI capture')
    def test_actual_request_has_no_tools_and_uses_empty_home(self):
        captured = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                captured.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"error":{"type":"local_test","message":"intentional capture stop"}}')
        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix='ticket-model-capability-test-') as folder:
                directory = Path(folder)
                (directory / 'codex-home').mkdir()
                (directory / 'model-home').mkdir()
                schema = directory / 'response.schema.json'
                schema.write_text(json.dumps(PLAN_SCHEMA))
                args = arguments(os.environ['AI_TICKET_CODEX_TEST_BIN'], directory, 'Return JSON only.', schema)[:-1]
                values = {
                    'model_provider': 'localtest', 'model_providers.localtest.name': 'loopback capture',
                    'model_providers.localtest.base_url': f'http://127.0.0.1:{server.server_port}/v1',
                    'model_providers.localtest.wire_api': 'responses',
                    'model_providers.localtest.requires_openai_auth': False,
                    'model_providers.localtest.request_max_retries': 0,
                    'model_providers.localtest.stream_max_retries': 0,
                    'features.enable_request_compression': False,
                    'features.system_proxy_fallback': False,
                    'features.respect_system_proxy': False,
                }
                for key, value in values.items(): args += ['-c', key + '=' + json.dumps(value)]
                result = subprocess.run(args + ['-'], input='{"synthetic_local_fixture":true}',
                    capture_output=True, text=True, timeout=25, cwd=directory, env={
                        'PATH': os.environ['PATH'], 'HOME': str(directory / 'model-home'),
                        'CODEX_HOME': str(directory / 'codex-home'), 'LANG': 'en_US.UTF-8',
                        'NO_PROXY': 'localhost,127.0.0.1,::1', 'no_proxy': 'localhost,127.0.0.1,::1'})
                self.assertTrue(captured, (result.stderr + result.stdout)[-3000:])
                for request in captured:
                    self.assertFalse(request.get('tools'), [item.get('name', item.get('type')) for item in request.get('tools', [])])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__': unittest.main()
