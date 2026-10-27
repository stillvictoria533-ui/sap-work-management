"""Follow-up review: preserve business-key relationships, never a Cartesian product."""
import itertools
import uuid

from django.test import SimpleTestCase, TestCase, override_settings

from . import ai
from .ai_gateway import dispatch
from .ai_policy import PolicyError, cli_arguments, freeze_scope, validate_plan
from .models import TicketAIQuery, TicketAIRun, User
from .services import create_ticket, change_ticket_status, reply_ticket


CASES = [
    ('BKPF', 'BELNR=1900000123 BUKRS=1000 GJAHR=2025\nBELNR=1900000456 BUKRS=2000 GJAHR=2026',
     [{'BELNR': '1900000123', 'BUKRS': '1000', 'GJAHR': '2025'},
      {'BELNR': '1900000456', 'BUKRS': '2000', 'GJAHR': '2026'}]),
    ('MARC', 'MATNR=12345 WERKS=1000\nMATNR=67890 WERKS=2000',
     [{'MATNR': '12345', 'WERKS': '1000'}, {'MATNR': '67890', 'WERKS': '2000'}]),
]


def plan(table, values):
    return {'command': 'data', 'object': table, 'fields': list(values), 'rows': 1,
            'filters': [{'field': field, 'value': value} for field, value in values.items()]}


class BusinessTuplePolicyTests(SimpleTestCase):
    def test_only_the_two_authorized_objects_pass_in_each_system(self):
        for system, (table, text, records) in itertools.product(['DS4', 'QS4', 'PS4'], CASES):
            scope = freeze_scope(system, table, text, '开始处理', 1)
            fields = list(records[0])
            for values in itertools.product(*(sorted({row[key] for row in records}) for key in fields)):
                record = dict(zip(fields, values))
                with self.subTest(system=system, table=table, values=record):
                    if record in records:
                        self.assertEqual(validate_plan(plan(table, record), scope)['object'], table)
                        self.assertIn('--filter', cli_arguments(plan(table, record), scope))
                    else:
                        with self.assertRaises(PolicyError):
                            validate_plan(plan(table, record), scope)
                        with self.assertRaises(PolicyError):
                            cli_arguments(plan(table, record), scope)

    def test_legacy_multiple_value_sets_require_relationship_confirmation(self):
        for table, text, records in CASES:
            scope = freeze_scope('PS4', table, text, '开始处理', 1)
            scope['schema'] = 'ticket-read-scope/v1'
            scope.pop('data_objects', None)
            for record in records:
                with self.subTest(table=table, values=record), self.assertRaises(PolicyError):
                    validate_plan(plan(table, record), scope)
            # Metadata/source reads retain the existing authorization.
            self.assertEqual(validate_plan({'command': 'ddic', 'object': table}, scope)['object'], table)

    def test_unpaired_lists_are_not_guessed_by_order_or_zipped(self):
        scope = freeze_scope('DS4', 'MARC',
            '物料 12345、物料 67890；工厂 1000、工厂 2000', '开始处理', 1)
        for material, plant in itertools.product(['12345', '67890'], ['1000', '2000']):
            with self.subTest(material=material, plant=plant), self.assertRaises(PolicyError):
                validate_plan(plan('MARC', {'MATNR': material, 'WERKS': plant}), scope)

    def test_padded_values_still_match_the_same_object(self):
        scope = freeze_scope('DS4', 'MARC', CASES[1][1], '开始处理', 1)
        self.assertEqual(validate_plan(plan('MARC', {'MATNR': '00000012345', 'WERKS': '1000'}), scope)['object'], 'MARC')

    def test_organization_keys_do_not_gain_unapproved_numeric_aliases(self):
        scope = freeze_scope('DS4', 'MARC', 'MATNR=12345 WERKS=1000', '开始处理', 1)
        with self.assertRaises(PolicyError):
            validate_plan(plan('MARC', {'MATNR': '12345', 'WERKS': '01000'}), scope)

    def test_chinese_labels_and_semicolon_records_preserve_relationships(self):
        scope = freeze_scope('QS4', 'BKPF',
            '会计凭证1900000123 公司代码1000 年度2025；会计凭证1900000456 公司代码2000 年度2026', '开始处理', 1)
        for record in CASES[0][2]:
            self.assertEqual(validate_plan(plan('BKPF', record), scope)['object'], 'BKPF')
        with self.assertRaises(PolicyError):
            validate_plan(plan('BKPF', {**CASES[0][2][0], 'BUKRS': '2000'}), scope)

    def test_single_key_lists_need_no_cross_field_relationship_including_legacy(self):
        scope = freeze_scope('DS4', 'MARA', 'MATNR=12345 MATNR=67890', '开始处理', 1)
        for version in ['ticket-read-scope/v2', 'ticket-read-scope/v1']:
            scope['schema'] = version
            for material in ['12345', '67890']:
                self.assertEqual(validate_plan(plan('MARA', {'MATNR': material}), scope)['object'], 'MARA')

    def test_a_partial_second_object_cannot_borrow_the_first_objects_plant(self):
        scope = freeze_scope('DS4', 'MARC', 'MATNR=12345 WERKS=1000\nMATNR=67890', '开始处理', 1)
        self.assertEqual(validate_plan(plan('MARC', {'MATNR': '12345', 'WERKS': '1000'}), scope)['object'], 'MARC')
        with self.assertRaises(PolicyError):
            validate_plan(plan('MARC', {'MATNR': '67890', 'WERKS': '1000'}), scope)


@override_settings(AI_TICKET_AUTOMATION_ENABLED=True, DINGTALK_ENABLED=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class BusinessTupleGatewayTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='tuple-admin', email='admin@tuple.test', name='管理员', role='admin')
        self.owner = User.objects.create_user(username='tuple-owner', email='owner@tuple.test', name='顾问')

    def make_lease(self, table, text):
        ticket = create_ticket(self.owner, {'request_key': uuid.uuid4(), 'title': table,
            'system': 'PS4', 'module': 'FI', 'kind': '问题', 'description': text}, [])[0]
        ticket = change_ticket_status(self.admin, ticket.pk, {'action': 'start', 'version': ticket.version})
        return ticket, ai.claim(ticket.pk, ticket.version)

    def followup(self, ticket, lease, text):
        ai.publish(lease['run_id'], lease['lease_token'], {'category': 'clarification', 'body': '请补充必要业务键。', 'evidence': []})
        ticket.refresh_from_db()
        reply_ticket(self.owner, ticket.pk, {'version': ticket.version, 'body': text}, [])
        ticket.refresh_from_db()
        return ai.claim(ticket.pk, ticket.version)

    def test_crossed_objects_never_create_a_query_reservation(self):
        for table, text, records in CASES:
            ticket, lease = self.make_lease(table, text)
            crossed = {**records[0], next(iter(records[0])): records[1][next(iter(records[0]))]}
            request = {'operation': 'query_reserve', 'run_id': lease['run_id'], 'lease_token': lease['lease_token']}
            before = TicketAIQuery.objects.count()
            with self.subTest(table=table), self.assertRaises(PolicyError):
                dispatch({**request, 'request_key': str(uuid.uuid4()), 'plan': plan(table, crossed)})
            self.assertEqual(TicketAIQuery.objects.count(), before)
            for record in records:
                result = dispatch({**request, 'request_key': str(uuid.uuid4()), 'plan': plan(table, record)})
                self.assertEqual(result['outcome'], 'reserved')
                stored = TicketAIQuery.objects.get(pk=result['query_id'])
                self.assertEqual(stored.plan, result['plan'])

    def test_missing_keys_are_completed_on_the_matching_object_once(self):
        ticket, lease = self.make_lease('MARC', 'MATNR=12345 WERKS=1000\nMATNR=67890')
        completed = self.followup(ticket, lease, 'MATNR=67890 WERKS=2000')
        scope = completed['read_scope']
        for record in CASES[1][2]:
            self.assertEqual(validate_plan(plan('MARC', record), scope)['object'], 'MARC')
        with self.assertRaises(PolicyError):
            validate_plan(plan('MARC', {'MATNR': '67890', 'WERKS': '1000'}), scope)
        later = self.followup(ticket, completed, 'MATNR=67890 WERKS=1000')
        self.assertEqual(later['read_scope']['data_objects'], scope['data_objects'])

    def test_ambiguous_completion_cannot_fill_two_objects_by_position(self):
        ticket, lease = self.make_lease('MARC', 'MATNR=12345\nMATNR=67890')
        ambiguous = self.followup(ticket, lease, 'WERKS=1000 WERKS=2000')
        self.assertNotIn('WERKS', ambiguous['read_scope']['document_keys'])
        completed = self.followup(ticket, ambiguous, CASES[1][1])
        for record in CASES[1][2]:
            self.assertEqual(validate_plan(plan('MARC', record), completed['read_scope'])['object'], 'MARC')

    def test_legacy_ambiguous_scope_is_not_repaired_by_untrusted_followup_pairing(self):
        ticket, lease = self.make_lease('MARC', 'MATNR=12345 MATNR=67890；WERKS=1000 WERKS=2000')
        run = TicketAIRun.objects.get(pk=lease['run_id'])
        run.accepted_scope['schema'] = 'ticket-read-scope/v1'
        run.accepted_scope.pop('data_objects')
        run.save(update_fields=['accepted_scope'])
        following = self.followup(ticket, lease, CASES[1][1])
        self.assertEqual(following['read_scope']['data_objects']['MARC'], [])
        with self.assertRaises(PolicyError):
            validate_plan(plan('MARC', CASES[1][2][0]), following['read_scope'])

    def test_legacy_pairs_are_recovered_from_the_unchanged_accepted_source(self):
        ticket, lease = self.make_lease('MARC', CASES[1][1])
        run = TicketAIRun.objects.get(pk=lease['run_id'])
        run.accepted_scope['schema'] = 'ticket-read-scope/v1'
        run.accepted_scope.pop('data_objects')
        run.save(update_fields=['accepted_scope'])
        following = self.followup(ticket, lease, 'MATNR=12345 WERKS=2000')
        for record in CASES[1][2]:
            self.assertEqual(validate_plan(plan('MARC', record), following['read_scope'])['object'], 'MARC')
        with self.assertRaises(PolicyError):
            validate_plan(plan('MARC', {'MATNR': '12345', 'WERKS': '2000'}), following['read_scope'])

    def test_legacy_source_hash_mismatch_cannot_recover_relationships(self):
        ticket, lease = self.make_lease('MARC', CASES[1][1])
        run = TicketAIRun.objects.get(pk=lease['run_id'])
        run.accepted_scope['schema'] = 'ticket-read-scope/v1'
        run.accepted_scope['source_hash'] = '0' * 64
        run.accepted_scope.pop('data_objects')
        run.save(update_fields=['accepted_scope'])
        following = self.followup(ticket, lease, CASES[1][1])
        self.assertEqual(following['read_scope']['data_objects']['MARC'], [])

    def test_missing_optional_constraint_is_completed_on_its_object_not_borrowed(self):
        ticket, lease = self.make_lease('MAKT', 'MATNR=12345 SPRAS=ZH\nMATNR=67890')
        completed = self.followup(ticket, lease, 'MATNR=67890 SPRAS=EN')
        scope = completed['read_scope']
        self.assertEqual(validate_plan(plan('MAKT', {'MATNR': '67890', 'SPRAS': 'EN'}), scope)['object'], 'MAKT')
        with self.assertRaises(PolicyError):
            validate_plan(plan('MAKT', {'MATNR': '12345', 'SPRAS': 'EN'}), scope)
