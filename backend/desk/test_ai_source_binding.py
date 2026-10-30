"""Synthetic exact-object metadata binding; never connects to SAP."""
import copy
import json
from pathlib import Path
import sys
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from . import ai
from .ai_gateway import dispatch
from .ai_policy import PolicyError, digest, freeze_scope, validate_plan
from .models import Audit, Delivery, Event, Notification, TicketAIRun, User
from .services import change_ticket_status, create_ticket, reply_ticket


@override_settings(AI_TICKET_AUTOMATION_ENABLED=True, DINGTALK_ENABLED=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SourceBindingTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='bind-admin@test.invalid', email='bind-admin@test.invalid',
            name='管理员', role='admin')
        self.owner = User.objects.create_user(username='bind-owner@test.invalid', email='bind-owner@test.invalid', name='顾问')
        self.ticket = create_ticket(self.owner, {'request_key': uuid.uuid4(), 'title': 'ZDEMO301接口排查',
            'description': '合成报文，返回没有数据。', 'system': 'PS4', 'module': 'MM', 'kind': '问题'}, [])[0]
        self.ticket = change_ticket_status(self.admin, self.ticket.pk, {'version': self.ticket.version, 'action': 'start'})
        self.lease = ai.claim(self.ticket.pk, self.ticket.version)
        self.auth = {key: self.lease[key] for key in ['run_id', 'lease_token']}

    def reserve(self, **extra):
        return dispatch({'operation': 'source_type_reserve', **self.auth, 'object': 'ZDEMO301',
            'request_key': str(uuid.uuid4()), **extra})

    def complete(self, reserved, matches):
        return dispatch({'operation': 'query_complete', **self.auth, 'query_id': reserved['query_id'], 'error': '',
            'result': {'system': 'PS4', 'client': '100', 'command': 'source-type', 'object': 'ZDEMO301',
                'sha256': 'a' * 64, 'source_type_matches': matches}})

    def resolve(self, matches=None):
        matches = {'TFDIR': ['fm']} if matches is None else matches
        checks = []
        for _ in range(4):
            reserved = self.reserve()
            self.assertEqual(reserved['outcome'], 'reserved')
            checks.append(reserved)
            result = self.complete(reserved, matches.get(reserved['plan']['table'], []))
        return checks, result

    def test_exact_metadata_uniquely_binds_ps4_without_granting_business_data_or_other_types(self):
        before = copy.deepcopy(self.lease['read_scope'])
        with self.assertRaises(PolicyError): validate_plan({'command': 'source', 'kind': 'fm', 'object': 'ZDEMO301'}, before)
        checks, result = self.resolve()
        self.assertEqual({item['plan']['table'] for item in checks}, {'DD02L', 'TRDIR', 'TFDIR', 'SEOCLASS'})
        self.assertEqual(result['outcome'], 'source_type_checked')
        self.assertEqual(result['binding'], 'bound')
        scope = TicketAIRun.objects.get(pk=self.lease['run_id']).accepted_scope
        self.assertEqual(scope['source_kinds']['ZDEMO301'], ['fm'])
        self.assertEqual(scope['objects'], before['objects'])
        self.assertEqual(scope['source_hash'], before['source_hash'])
        self.assertEqual(scope['system'], 'PS4')
        self.assertEqual(scope['client'], '100')
        self.assertEqual(validate_plan({'command': 'source', 'kind': 'fm', 'object': 'ZDEMO301'}, scope)['kind'], 'fm')
        for plan in [{'command': 'source', 'kind': 'prog', 'object': 'ZDEMO301'},
                     {'command': 'source', 'kind': 'fm', 'object': 'ZOTHER999'},
                     {'command': 'source-type', 'object': 'ZDEMO301', 'table': 'TFDIR'},
                     {'command': 'data', 'object': 'TFDIR', 'fields': ['FUNCNAME'],
                      'filters': [{'field': 'FUNCNAME', 'value': 'ZDEMO301'}], 'rows': 2}]:
            with self.assertRaises(PolicyError): validate_plan(plan, scope)
        self.assertEqual(TicketAIRun.objects.get(pk=self.lease['run_id']).queries.count(), 4)
        self.assertTrue(Audit.objects.filter(action='ai_ticket_source_type_bound', target=str(self.ticket.pk)).exists())
        self.assertNotIn('evidence', result)

    def test_ambiguous_table_collision_and_interface_do_not_bind(self):
        for matches in [{'TFDIR': ['fm'], 'TRDIR': ['prog']}, {'TFDIR': ['fm'], 'DD02L': ['table']},
                        {'SEOCLASS': ['interface']}, {'TFDIR': ['fm', 'fm']}]:
            with self.subTest(matches=matches):
                # Each attempt is a separate accepted synthetic ticket, not a reset of query history.
                if TicketAIRun.objects.get(pk=self.auth['run_id']).queries.exists():
                    self.new_ticket()
                _, result = self.resolve(matches)
                self.assertIn(result['binding'], ['ambiguous', 'unresolved'])
                scope = TicketAIRun.objects.get(pk=self.auth['run_id']).accepted_scope
                self.assertNotIn('ZDEMO301', scope['source_kinds'])

    def new_ticket(self):
        ticket = create_ticket(self.owner, {'request_key': uuid.uuid4(), 'title': 'ZDEMO301接口排查',
            'description': '合成材料', 'system': 'PS4', 'module': 'MM', 'kind': '问题'}, [])[0]
        self.ticket = change_ticket_status(self.admin, ticket.pk, {'version': ticket.version, 'action': 'start'})
        self.lease = ai.claim(self.ticket.pk, self.ticket.version)
        self.auth = {key: self.lease[key] for key in ['run_id', 'lease_token']}

    def test_unknown_metadata_cannot_become_a_business_reply_receipt(self):
        checks, _ = self.resolve()
        payload = {'category': 'query', 'body': '不能以目录匹配代替接口分析。', 'evidence': [
            {'system': 'PS4', 'client': '100', 'reference': 'sap-ticket-query:' + checks[0]['query_id']}]}
        with self.assertRaises(ValidationError): ai.publish(self.auth['run_id'], self.auth['lease_token'], payload)

    def test_old_scope_recovers_only_original_title_targets_not_followup_or_payload_keys(self):
        run = TicketAIRun.objects.get(pk=self.auth['run_id'])
        run.accepted_scope.pop('source_type_objects', None)
        run.save(update_fields=['accepted_scope'])
        reply_ticket(self.owner, self.ticket.pk, {'version': self.ticket.version, 'body': '新增接口 ZOTHER999'}, [])
        self.ticket.refresh_from_db()
        lease = ai.claim(self.ticket.pk, self.ticket.version)
        self.assertEqual(lease['read_scope']['source_type_objects'], ['ZDEMO301'])
        self.assertNotIn('ZOTHER999', lease['read_scope']['objects'])
        scope = freeze_scope('PS4', 'ZDEMO301接口排查', '{"IV_GUID":"SYNTHETIC001"}', '开始处理', 1)
        self.assertEqual(scope['source_type_objects'], ['ZDEMO301'])

    def test_restrictions_changed_material_outside_objects_and_extra_options_reject(self):
        for values in [{'object': 'ZOTHER999'}, {'object': 'ZDEMO301', 'table': 'TFDIR'},
                       {'object': 'ZDEMO301', 'kind': 'fm'}]:
            with self.subTest(values=values), self.assertRaises(PolicyError): self.reserve(**values)
        self.ticket.description += ' changed'
        self.ticket.save(update_fields=['description'])
        with self.assertRaises(PolicyError): self.reserve()
        self.assertEqual(TicketAIRun.objects.get(pk=self.auth['run_id']).queries.count(), 0)

    def test_each_lookup_consumes_original_budget_and_requires_time_for_source_read(self):
        run = TicketAIRun.objects.get(pk=self.auth['run_id'])
        for _ in range(4):
            run.queries.create(request_key=uuid.uuid4(), context_version=run.context_version,
                lease_token=run.lease_token, plan={'command': 'ddic', 'object': 'ZDEMO301'},
                plan_hash='b' * 64, deadline_at=timezone.now() + timedelta(seconds=60))
        result = self.reserve()
        self.assertEqual(result['outcome'], 'source_type_unavailable')
        self.assertEqual(run.queries.count(), 4)

    def test_metadata_and_source_share_the_original_eight_query_limit(self):
        self.resolve()
        for _ in range(4):
            dispatch({'operation': 'query_reserve', **self.auth, 'request_key': str(uuid.uuid4()),
                'plan': {'command': 'source', 'object': 'ZDEMO301', 'kind': 'fm'}})
        run = TicketAIRun.objects.get(pk=self.auth['run_id'])
        self.assertEqual(run.queries.count(), 8)
        with self.assertRaisesRegex(PolicyError, 'query_limit'):
            dispatch({'operation': 'query_reserve', **self.auth, 'request_key': str(uuid.uuid4()),
                'plan': {'command': 'source', 'object': 'ZDEMO301', 'kind': 'fm'}})

    def test_repeated_reservation_and_completion_never_replays_or_rebinds(self):
        key = str(uuid.uuid4())
        reserved = self.reserve(request_key=key)
        repeated = self.reserve(request_key=key)
        self.assertEqual(repeated['outcome'], 'already_reserved')
        self.assertEqual(repeated['query_id'], reserved['query_id'])
        self.complete(reserved, [])
        with self.assertRaises(PolicyError): self.complete(reserved, ['table'])
        self.assertEqual(TicketAIRun.objects.get(pk=self.auth['run_id']).queries.count(), 1)

    def test_wrong_type_or_lost_lease_never_binds(self):
        reserved = self.reserve()
        with self.assertRaises(PolicyError): self.complete(reserved, ['fm'])  # first lookup is DD02L
        run = TicketAIRun.objects.get(pk=self.auth['run_id'])
        run.lease_token = uuid.uuid4(); run.save(update_fields=['lease_token'])
        with self.assertRaises(PolicyError): self.complete(reserved, [])
        self.assertNotIn('ZDEMO301', run.accepted_scope['source_kinds'])

    def test_absent_expired_or_failed_metadata_does_not_bind_or_replay(self):
        _, result = self.resolve({})
        self.assertEqual(result['binding'], 'unresolved')
        self.assertEqual(self.reserve()['outcome'], 'source_type_unavailable')
        self.new_ticket()
        reserved = self.reserve()
        self.complete(reserved, [])
        run = TicketAIRun.objects.get(pk=self.auth['run_id'])
        run.queries.update(finished_at=timezone.now() - timedelta(minutes=6))
        self.assertEqual(self.reserve()['outcome'], 'source_type_unavailable')
        self.assertEqual(run.queries.count(), 1)
        self.new_ticket()
        reserved = self.reserve()
        run = TicketAIRun.objects.get(pk=self.auth['run_id'])
        run.queries.update(deadline_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.complete(reserved, []), {'outcome': 'failed', 'reason': 'time_limit'})
        self.assertEqual(self.reserve()['outcome'], 'source_type_unavailable')
        run.refresh_from_db()
        self.assertNotIn('ZDEMO301', run.accepted_scope['source_kinds'])

    def test_disabled_expired_restricted_or_preempted_round_cannot_start_lookup(self):
        with override_settings(AI_TICKET_AUTOMATION_ENABLED=False), self.assertRaises(PolicyError): self.reserve()
        run = TicketAIRun.objects.get(pk=self.auth['run_id'])
        run.deadline_at = timezone.now() + timedelta(minutes=4); run.save(update_fields=['deadline_at'])
        with self.assertRaisesRegex(PolicyError, 'time_limit'): self.reserve()
        run.deadline_at = timezone.now() + timedelta(minutes=40)
        run.accepted_scope['restriction_unresolved'] = True
        run.save(update_fields=['deadline_at', 'accepted_scope'])
        with self.assertRaisesRegex(PolicyError, 'scope_unresolved'): self.reserve()
        run.accepted_scope['restriction_unresolved'] = False; run.save(update_fields=['accepted_scope'])
        reply_ticket(self.admin, self.ticket.pk, {'version': self.ticket.version, 'body': '人工答复'}, [])
        with self.assertRaisesRegex(PolicyError, 'run_not_live'): self.reserve()
        self.assertEqual(run.queries.count(), 0)

    def test_real_gateway_reader_and_coordinator_complete_metadata_source_and_reply_once(self):
        scripts = str(Path(__file__).resolve().parents[2] / 'scripts')
        if scripts not in sys.path: sys.path.insert(0, scripts)
        from ai_ticket_worker import SAPReader, TicketProcessor
        def gateway(operation, **values): return dispatch({'operation': operation, **values})
        contexts = []
        def planner(context, pulse):
            contexts.append(copy.deepcopy(context))
            if not context['observations']:
                return {'action': 'query', 'plan': {'command': 'source', 'object': 'ZDEMO301',
                    'kind': 'function', 'method': '', 'fields': [], 'filters': [], 'rows': 0},
                    'body': '', 'evidence_ids': [], 'reason': ''}
            return {'action': 'reply', 'plan': None, 'body': '仅合成源码证据结论。',
                'evidence_ids': [context['observations'][0]['evidence_id']], 'reason': ''}
        def execute(args, **values):
            request = json.loads(values['input_text'])
            cli = request['args']
            if cli[0] == 'data':
                table = cli[1]
                rows = [{'FUNCNAME': 'ZDEMO301'}] if table == 'TFDIR' else []
                content = {'table': table, 'row_limit': 2, 'row_count': len(rows), 'rows': rows}
            else:
                self.assertEqual(cli, ['source', 'fm', 'ZDEMO301', '--conn', 'PS4'])
                content = {'source': 'synthetic only'}
            return json.dumps({**content, '_evidence': {'system': 'PS4', 'client': '100', 'command': cli[0],
                'object': cli[1] if cli[0] == 'data' else 'ZDEMO301', 'trust': 'UNTRUSTED_EVIDENCE',
                'instructionAuthority': False, 'contentSha256': digest(content)}})
        counts = Notification.objects.count(), Delivery.objects.count()
        with patch('ai_ticket_worker.run_bounded', side_effect=execute) as execute_sap:
            result = TicketProcessor(gateway, planner, SAPReader('/fixed/node', '/fixed/company', 'FIXED_USER')).process(self.lease)
        self.assertEqual(result['outcome'], 'replied')
        self.assertEqual(execute_sap.call_count, 5)
        self.assertEqual((Notification.objects.count(), Delivery.objects.count()), (counts[0] + 1, counts[1] + 1))
        self.assertEqual(len(contexts[-1]['observations']), 1)
        self.assertEqual(contexts[-1]['queries_remaining'], 3)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, 'in-progress')
        run = TicketAIRun.objects.get(pk=self.auth['run_id'])
        self.assertEqual(run.queries.count(), 5)
        self.assertEqual(run.status, 'replied')
        self.assertEqual(Event.objects.filter(ticket=self.ticket, is_ai=True).count(), 1)

    def test_production_class_still_requires_accepted_method(self):
        _, result = self.resolve({'SEOCLASS': ['class']})
        with self.assertRaisesRegex(PolicyError, 'production_method_required'):
            validate_plan({'command': 'source', 'kind': 'class', 'object': 'ZDEMO301'}, result['read_scope'])

    def test_ambiguous_lookup_stops_coordinator_and_requires_manual_action(self):
        scripts = str(Path(__file__).resolve().parents[2] / 'scripts')
        if scripts not in sys.path: sys.path.insert(0, scripts)
        from ai_ticket_worker import TicketProcessor
        def gateway(operation, **values): return dispatch({'operation': operation, **values})
        def planner(context, pulse):
            return {'action': 'query', 'plan': {'command': 'source', 'object': 'ZDEMO301', 'kind': ''},
                'body': '', 'evidence_ids': [], 'reason': ''}
        def reader(reserved):
            self.assertEqual(reserved['plan']['command'], 'source-type')
            matches = {'TFDIR': ['fm'], 'TRDIR': ['prog']}.get(reserved['plan']['table'], [])
            return {}, {'system': 'PS4', 'client': '100', 'object': 'ZDEMO301', 'command': 'source-type',
                'sha256': 'a' * 64, 'source_type_matches': matches}
        result = TicketProcessor(gateway, planner, reader).process(self.lease)
        self.assertEqual(result, {'outcome': 'blocked', 'reason': 'authorization_missing', 'manual_required': True})
        self.assertEqual(ai.ticket_ai_state(self.ticket)['state'], 'manual_required')
        self.assertIn('未唯一匹配', ai.ticket_ai_state(self.ticket)['message'])
        self.assertFalse(Event.objects.filter(ticket=self.ticket, is_ai=True).exists())
        self.assertEqual(TicketAIRun.objects.get(pk=self.auth['run_id']).queries.count(), 4)
