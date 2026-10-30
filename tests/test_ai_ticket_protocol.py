"""Protocol and fixed metadata adapter regression, with synthetic inputs only."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'backend'))
from ai_ticket_model import PLAN_SCHEMA
from ai_ticket_worker import normalize_decision, SAPReader, TicketProcessor, WorkerError
from desk.ai_policy import PolicyError, digest, freeze_scope, validate_plan


def source_decision(kind='function'):
    return {'action': 'query', 'plan': {'command': 'source', 'object': 'ZDEMO301', 'kind': kind,
        'method': '', 'fields': [], 'filters': [], 'rows': 0}, 'body': '', 'evidence_ids': [], 'reason': ''}


class ModelProtocolTests(unittest.TestCase):
    def test_observed_function_alias_normalizes_but_execution_policy_stays_closed(self):
        scope = freeze_scope('PS4', '函数模块 ZDEMO301', '合成材料', '开始处理', 1)
        decision = normalize_decision(source_decision())
        self.assertEqual(validate_plan(decision['plan'], scope)['kind'], 'fm')
        with self.assertRaisesRegex(PolicyError, 'invalid_source_kind'):
            validate_plan({'command': 'source', 'object': 'ZDEMO301', 'kind': 'function'}, scope)
        for kind in ['FUNCTION', 'function;execute', 'rfc', '../fm', 'fm --conn DS4']:
            with self.subTest(kind=kind), self.assertRaises(PolicyError):
                normalize_decision(source_decision(kind))

    def test_schema_rejects_the_observed_alias_at_generation_boundary(self):
        variants = [item for item in PLAN_SCHEMA['properties']['plan']['anyOf'] if item.get('type') == 'object']
        for variant in variants:
            values = variant['properties']['kind'].get('enum', [])
            self.assertTrue(values)
            self.assertNotIn('function', values)
            self.assertLessEqual(set(values), {'', 'fm', 'prog', 'class'})

    def test_unused_options_and_scope_mismatches_are_still_rejected(self):
        decision = source_decision()
        decision['plan']['rows'] = 20
        with self.assertRaises(PolicyError): normalize_decision(decision)
        scope = freeze_scope('PS4', '程序 ZDEMO301', '合成材料', '开始处理', 1)
        with self.assertRaises(PolicyError): validate_plan(normalize_decision(source_decision())['plan'], scope)

    def test_unknown_kind_is_not_inferred_from_object_name(self):
        scope = freeze_scope('PS4', 'ZDEMO301接口排查', '合成材料', '开始处理', 1)
        self.assertEqual(scope['source_kinds'], {})
        self.assertEqual(scope['source_type_objects'], ['ZDEMO301'])
        with self.assertRaises(PolicyError): validate_plan(normalize_decision(source_decision())['plan'], scope)


class MetadataReaderTests(unittest.TestCase):
    def reserved(self):
        return {'plan': {'command': 'source-type', 'object': 'ZDEMO301', 'table': 'TFDIR'},
            'scope': freeze_scope('PS4', 'ZDEMO301接口排查', '合成材料', '开始处理', 1), 'timeout_seconds': 60,
            'deadline_at': (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()}

    def envelope(self):
        content = {'table': 'TFDIR', 'row_limit': 2, 'row_count': 1, 'rows': [{'FUNCNAME': 'ZDEMO301'}]}
        return {**content, '_evidence': {'system': 'PS4', 'client': '100', 'command': 'data', 'object': 'TFDIR',
            'trust': 'UNTRUSTED_EVIDENCE', 'instructionAuthority': False, 'contentSha256': digest(content)}}

    def test_metadata_uses_exact_projected_key_read_and_verifies_envelope(self):
        envelope = self.envelope()
        with patch('ai_ticket_worker.run_bounded', return_value=json.dumps(envelope)) as execute:
            _, result = SAPReader('/fixed/node', '/fixed/company', 'FIXED_USER')(self.reserved())
        request = json.loads(execute.call_args.kwargs['input_text'])
        self.assertEqual(request['args'], ['data', 'TFDIR', '--conn', 'PS4', '--fields', 'FUNCNAME',
            '--rows', '2', '--filter', 'FUNCNAME=ZDEMO301'])
        self.assertEqual(result['source_type_matches'], ['fm'])
        self.assertEqual((result['command'], result['object']), ('source-type', 'ZDEMO301'))

    def test_injected_rows_hash_mismatch_or_excess_rows_are_rejected(self):
        for mode in ['object', 'hash', 'limit', 'projection']:
            envelope = self.envelope()
            if mode == 'object': envelope['rows'][0]['FUNCNAME'] = 'ZOTHER999'
            if mode == 'hash': envelope['_evidence']['contentSha256'] = '0' * 64
            if mode == 'limit': envelope['rows'] *= 3; envelope['row_count'] = 3
            if mode == 'projection': envelope['rows'][0]['BODY'] = 'injected'
            if mode != 'hash':
                envelope['_evidence']['contentSha256'] = digest({key: value for key, value in envelope.items() if key != '_evidence'})
            with self.subTest(mode=mode), patch('ai_ticket_worker.run_bounded', return_value=json.dumps(envelope)):
                with self.assertRaises(WorkerError): SAPReader('/fixed/node', '/fixed/company')(self.reserved())

    def test_model_cannot_select_metadata_table_fields_or_new_object(self):
        for change in [{'table': 'USR02'}, {'object': 'ZOTHER999'}, {'fields': ['PASSWORD']}]:
            reserved = self.reserved(); reserved['plan'].update(change)
            with self.subTest(change=change), patch('ai_ticket_worker.run_bounded') as execute:
                with self.assertRaises(PolicyError): SAPReader('/fixed/node', '/fixed/company')(reserved)
                execute.assert_not_called()

    def test_metadata_system_client_command_and_trust_cannot_be_substituted(self):
        for key, value in [('system', 'DS4'), ('client', '110'), ('command', 'search'),
                           ('object', 'TRDIR'), ('instructionAuthority', True), ('trust', 'TRUSTED')]:
            envelope = self.envelope(); envelope['_evidence'][key] = value
            with self.subTest(key=key), patch('ai_ticket_worker.run_bounded', return_value=json.dumps(envelope)):
                with self.assertRaises(WorkerError): SAPReader('/fixed/node', '/fixed/company')(self.reserved())


class MetadataCoordinatorTests(unittest.TestCase):
    def test_missing_kind_uses_trusted_lookup_then_original_validated_source_plan(self):
        scope = freeze_scope('PS4', 'ZDEMO301接口排查', '合成材料', '开始处理', 1)
        bound = copy.deepcopy(scope); bound['source_kinds']['ZDEMO301'] = ['fm']
        lease = {'run_id': 'secret-run', 'lease_token': 'secret-token', 'queries_used': 0,
            'deadline_at': (datetime.now(timezone.utc) + timedelta(minutes=40)).isoformat(),
            'read_scope': scope, 'context': {'description': 'synthetic'}}
        tables = iter(['DD02L', 'TRDIR', 'TFDIR', 'SEOCLASS'])
        completed = [0]
        def gateway(operation, **values):
            if operation == 'renew': return {'outcome': 'renewed'}
            if operation == 'source_type_reserve':
                return {'outcome': 'reserved', 'query_id': str(completed[0]), 'scope': scope,
                    'plan': {'command': 'source-type', 'object': 'ZDEMO301', 'table': next(tables)},
                    'timeout_seconds': 60, 'deadline_at': (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()}
            if operation == 'query_complete' and values['result']['command'] == 'source-type':
                completed[0] += 1
                return {'outcome': 'source_type_checked', 'binding': 'bound' if completed[0] == 4 else 'pending',
                    'read_scope': bound if completed[0] == 4 else scope, 'queries_used': completed[0]}
            if operation == 'query_reserve':
                self.assertEqual(values['plan'], {'command': 'source', 'object': 'ZDEMO301', 'kind': 'fm'})
                return {'outcome': 'reserved', 'query_id': 'source', 'scope': bound, 'plan': values['plan'],
                    'timeout_seconds': 60, 'deadline_at': (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()}
            if operation == 'query_complete':
                return {'outcome': 'recorded', 'collected_at': 'synthetic', 'evidence': {'reference': 'actual-source'}}
            return {'outcome': 'replied'}
        def reader(reserved):
            return {'synthetic': True}, {'command': reserved['plan']['command']}
        planner = Mock(side_effect=[source_decision(), {'action': 'reply', 'plan': None,
            'body': '合成结论', 'evidence_ids': ['source'], 'reason': ''}])
        caller = Mock(side_effect=gateway)
        result = TicketProcessor(caller, planner, Mock(side_effect=reader)).process(lease)
        self.assertEqual(result['outcome'], 'replied')
        self.assertEqual(completed[0], 4)
        next_input = planner.call_args_list[1].args[0]
        self.assertEqual(next_input['queries_remaining'], 3)
        self.assertEqual(next_input['read_scope']['source_kinds']['ZDEMO301'], ['fm'])
        self.assertEqual(len(next_input['observations']), 1)
        self.assertNotIn('secret-token', json.dumps(next_input))
        self.assertNotIn('secret-run', json.dumps(next_input))


@unittest.skipUnless(shutil.which('node'), 'Node.js is required for the fixed transport test')
class MetadataTransportTests(unittest.TestCase):
    def test_metadata_operation_accepts_only_object_and_opaque_lease_fields(self):
        source = r'''
const assert=require('node:assert/strict');
const {buildCommand}=require('./scripts/ai_ticket_transport.cjs');
const request={operation:'source_type_reserve',run_id:'synthetic',lease_token:'synthetic',request_key:'synthetic',object:'ZDEMO301'};
const command=buildCommand(request);
assert(!command.includes('ZDEMO301'));
const encoded=command.match(/printf "%s" "([A-Za-z0-9+/=]+)"/)[1];
assert.deepEqual(JSON.parse(Buffer.from(encoded,'base64')),request);
for(const extra of [{table:'USR02'},{kind:'fm'},{fields:['PASSWORD']},{system:'DS4'},{Command:'injected'}])
 assert.throws(()=>buildCommand({...request,...extra}));
'''
        subprocess.run(['node', '-e', source], cwd=ROOT, check=True, capture_output=True, text=True, timeout=10)


if __name__ == '__main__': unittest.main()
