import copy
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from . import ai
from .ai_gateway import dispatch
from .ai_policy import PolicyError, digest, freeze_scope, validate_plan, validate_catalog_attestation
from .models import Audit, Delivery, Event, Notification, TicketAIRun, TicketAIQuery, User
from .services import change_ticket_status, create_ticket, reply_ticket


def metadata(name='ZDEMO001', kind='fm', system='DS4', client='110'):
    rows = {'table': [], 'prog': [], 'fm': [], 'class': []}
    rows[kind] = [{'name': name, 'remote': False, 'text': 'synthetic metadata'}]
    content = {'pattern': name, 'results': rows}
    return {**content, '_evidence': {'trust': 'UNTRUSTED_EVIDENCE', 'instructionAuthority': False,
        'system': system, 'client': client, 'user': 'TEST', 'command': 'search',
        'object': name, 'contentSha256': digest(content)}}


class ExplicitKindRecognitionTests(TestCase):
    def test_type_can_be_explicit_before_or_after_the_exact_object(self):
        for text in ['函数模块 ZDEMO001', 'ZDEMO001 函数模块', 'ZDEMO001（函数模块）']:
            with self.subTest(text=text):
                scope = freeze_scope('DS4', text, 'synthetic', '开始处理', 1)
                self.assertEqual(scope['source_kinds'].get('ZDEMO001'), ['fm'])
        scope = freeze_scope('DS4', 'ZDEMO001接口问题', 'synthetic', '开始处理', 1)
        self.assertNotIn('ZDEMO001', scope['source_kinds'])
        with self.assertRaises(PolicyError):
            validate_plan({'command': 'source', 'object': 'ZDEMO001', 'kind': 'fm'}, scope)
        # An overlapping, different identifier cannot authorize the suffix object.
        scope = freeze_scope('DS4', 'ZFOO 排查；XXZFOO函数模块', 'synthetic', '开始处理', 1)
        self.assertNotIn('ZFOO', scope['source_kinds'])
        self.assertEqual(scope['source_kinds'].get('XXZFOO'), ['fm'])
        for text in ['ZFOO\n函数模块 ZBAR', 'ZFOO 函数模块 ZBAR']:
            separated = freeze_scope('DS4', text, 'synthetic', '开始处理', 1)
            self.assertNotIn('ZFOO', separated['source_kinds'])
            self.assertEqual(separated['source_kinds'].get('ZBAR'), ['fm'])
        restricted = freeze_scope('DS4', 'ZFOO 函数模块', '禁止读取 ZFOO 的源码', '开始处理', 1)
        self.assertTrue(restricted['restriction_unresolved'])
        with self.assertRaisesRegex(PolicyError, 'scope_unresolved'):
            validate_plan({'command':'source','object':'ZFOO','kind':'fm'},restricted)


@override_settings(AI_TICKET_AUTOMATION_ENABLED=True, DINGTALK_ENABLED=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class OriginalKindRecoveryTests(TestCase):
    def setUp(self):
        self.admin=User.objects.create_user(username='recover-admin@test.invalid',email='recover-admin@test.invalid',name='管理员',role='admin')
        self.owner=User.objects.create_user(username='recover-owner@test.invalid',email='recover-owner@test.invalid',name='顾问')

    def test_missing_kind_can_be_recovered_only_from_unchanged_original_material(self):
        for schema in ['ticket-read-scope/v1','ticket-read-scope/v2']:
            with self.subTest(schema=schema):
                ticket=create_ticket(self.owner,{'request_key':uuid.uuid4(),'title':'ZDEMO002 函数模块',
                    'description':'synthetic','system':'DS4','module':'MM','kind':'问题'},[])[0]
                ticket=change_ticket_status(self.admin,ticket.pk,{'version':ticket.version,'action':'start'})
                prior=TicketAIRun.objects.get(ticket=ticket); prior.accepted_scope['source_kinds']={}
                prior.accepted_scope['schema']=schema;prior.save(update_fields=['accepted_scope'])
                recovered=ai.accepted_scope(ticket,prior.accepted_event)
                self.assertEqual(recovered['source_kinds']['ZDEMO002'],['fm'])
                self.assertEqual(validate_plan({'command':'source','object':'ZDEMO002','kind':'fm'},recovered)['kind'],'fm')
                with self.assertRaises(PolicyError):validate_plan({'command':'source','object':'ZDEMO002','kind':'prog'},recovered)
                prior.accepted_scope['source_hash']='0'*64;prior.save(update_fields=['accepted_scope'])
                self.assertNotIn('ZDEMO002',ai.accepted_scope(ticket,prior.accepted_event)['source_kinds'])

    def test_legacy_scope_does_not_discard_an_explicit_source_read_restriction(self):
        ticket=create_ticket(self.owner,{'request_key':uuid.uuid4(),'title':'ZDEMO002 函数模块',
            'description':'禁止读取 ZDEMO002 的源码','system':'DS4','module':'MM','kind':'问题'},[])[0]
        ticket=change_ticket_status(self.admin,ticket.pk,{'version':ticket.version,'action':'start'})
        prior=TicketAIRun.objects.get(ticket=ticket)
        prior.accepted_scope['schema']='ticket-read-scope/v1';prior.accepted_scope['source_kinds']={}
        prior.accepted_scope['restriction_unresolved']=False;prior.save(update_fields=['accepted_scope'])
        recovered=ai.accepted_scope(ticket,prior.accepted_event)
        self.assertTrue(recovered['restriction_unresolved'])
        with self.assertRaisesRegex(PolicyError,'scope_unresolved'):
            validate_plan({'command':'source','object':'ZDEMO002','kind':'fm'},recovered)

@override_settings(AI_TICKET_AUTOMATION_ENABLED=True, DINGTALK_ENABLED=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ManualFollowupTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='scope-admin@test.invalid',email='scope-admin@test.invalid',
            name='管理员',role='admin',must_change_password=False)
        self.owner = User.objects.create_user(username='scope-owner@test.invalid',email='scope-owner@test.invalid',
            name='顾问',role='consultant',must_change_password=False)
        ticket = create_ticket(self.owner, {'request_key':uuid.uuid4(),'title':'ZDEMO001接口报错',
            'description':'需要提示具体业务对象','system':'DS4','module':'MM','kind':'问题'}, [])[0]
        self.ticket = change_ticket_status(self.admin,ticket.pk,{'action':'start','version':ticket.version})
        self.lease = ai.claim(self.ticket.pk,self.ticket.version)
        self.old = TicketAIRun.objects.get(pk=self.lease['run_id'])
        self.old.status='blocked'; self.old.reason='time_limit'; self.old.attempts=3
        self.old.deadline_at=timezone.now()-timedelta(minutes=1)
        self.old.accepted_scope['schema']='ticket-read-scope/v1'
        self.old.accepted_scope.pop('data_objects',None)
        self.old.save()
        Audit.objects.create(actor=self.admin,action='ai_ticket_blocked',target=str(self.ticket.pk),
            detail={'run_id':str(self.old.pk),'reason':'authorization_missing','manual_required':True})

    def proof(self, evidence=None):
        return {'object':'ZDEMO001','kind':'fm','observed_at':timezone.now().isoformat(),
                'evidence':evidence or metadata()}

    def request(self, key=None, proof=None, version=None):
        return ai.request_blocked_followup(self.ticket.pk,self.ticket.version if version is None else version,
            key or uuid.uuid4(), proof or self.proof())

    def test_terminal_status_keeps_the_last_recorded_reason(self):
        status=ai.ticket_ai_state(self.ticket)
        self.assertEqual(status['state'],'manual_required')
        self.assertIn('45 分钟',status['message'])
        self.assertIn('身份或范围校验受阻',status['message'])

    def test_same_object_verified_type_can_start_one_audited_new_round(self):
        old_before=TicketAIRun.objects.filter(pk=self.old.pk).values().get()
        event_count=Event.objects.filter(ticket=self.ticket).count()
        notifications=(Notification.objects.count(),Delivery.objects.count())
        key=uuid.uuid4(); proof=self.proof(); requested=self.request(key=key,proof=proof)
        self.assertEqual(requested['outcome'],'queued')
        self.assertEqual(TicketAIRun.objects.filter(pk=self.old.pk).values().get(),old_before)
        self.assertEqual((Notification.objects.count(),Delivery.objects.count()),notifications)
        self.assertEqual(Event.objects.filter(ticket=self.ticket).count(),event_count+1)
        event=Event.objects.get(ticket=self.ticket,kind='ai_retry')
        self.assertEqual((event.actor_name,event.actor_role),('系统','system'))
        audit=Audit.objects.filter(action='ai_ticket_retry_requested',target=str(self.ticket.pk)).latest('id')
        self.assertEqual(audit.detail['source'],'explicit_blocked_operator_request')
        self.ticket.refresh_from_db()
        self.assertEqual(ai.candidate_page()['results'][0]['id'],self.ticket.pk)
        lease=ai.claim(self.ticket.pk,self.ticket.version)
        self.assertEqual(lease['outcome'],'claimed')
        self.assertEqual(lease['read_scope']['source_kinds']['ZDEMO001'],['fm'])
        self.assertEqual(lease['queries_used'],0)
        request={'operation':'query_reserve','run_id':lease['run_id'],'lease_token':lease['lease_token'],
                 'request_key':str(uuid.uuid4()),'plan':{'command':'source','object':'ZDEMO001','kind':'fm'}}
        self.assertEqual(dispatch(request)['outcome'],'reserved')
        for plan in [{'command':'source','object':'ZDEMO001','kind':'prog'},
                     {'command':'source','object':'ZOTHER999','kind':'fm'}]:
            with self.assertRaises(PolicyError): dispatch({**request,'request_key':str(uuid.uuid4()),'plan':plan})
        before=TicketAIRun.objects.filter(pk=lease['run_id']).values().get()
        repeated=self.request(key=key,proof=proof,version=self.ticket.version-1)
        self.assertEqual(repeated['outcome'],'already_requested')
        self.assertEqual(TicketAIRun.objects.filter(pk=lease['run_id']).values().get(),before)
        self.assertEqual(TicketAIQuery.objects.filter(run_id=lease['run_id']).count(),1)

    def test_existing_query_history_cannot_be_given_a_fresh_budget(self):
        TicketAIQuery.objects.create(run=self.old,request_key=uuid.uuid4(),context_version=self.old.context_version,
            lease_token=self.old.lease_token,plan={'command':'ddic','object':'ZDEMO001'},plan_hash='a'*64,
            status='failed',deadline_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.request(),{'outcome':'skipped','reason':'prior_query_history'})
        self.assertEqual(TicketAIRun.objects.filter(ticket=self.ticket).count(),1)

    def test_still_running_expired_round_is_not_manually_requeued(self):
        self.old.status='running';self.old.save(update_fields=['status'])
        self.assertEqual(self.request(),{'outcome':'skipped','reason':'not_manual_required'})
        self.assertEqual(TicketAIRun.objects.filter(ticket=self.ticket).count(),1)

    def test_reused_request_key_cannot_change_its_proof_or_restart_the_run(self):
        key=uuid.uuid4(); proof=self.proof(); requested=self.request(key=key,proof=proof)
        changed=copy.deepcopy(proof);changed['kind']='prog'
        with self.assertRaises(PolicyError):self.request(key=key,proof=changed)
        self.assertEqual(TicketAIRun.objects.filter(ticket=self.ticket).count(),2)
        self.assertEqual(TicketAIRun.objects.get(pk=requested['run_id']).attempts,0)

    def test_changed_original_material_and_wrong_version_are_rejected(self):
        self.assertEqual(self.request(version=self.ticket.version-1)['reason'],'version_changed')
        self.ticket.description='changed after acceptance';self.ticket.save(update_fields=['description'])
        with self.assertRaisesRegex(PolicyError,'accepted_material_mismatch'):self.request()
        self.assertEqual(TicketAIRun.objects.filter(ticket=self.ticket).count(),1)

    def test_stale_prior_context_cannot_open_a_new_round(self):
        self.ticket.version+=1;self.ticket.save(update_fields=['version'])
        result=self.request()
        self.assertEqual(result,{'outcome':'skipped','reason':'version_changed'})
        self.assertEqual(TicketAIRun.objects.filter(ticket=self.ticket).count(),1)

    def test_owner_followup_supersedes_the_manual_run_without_changing_accepted_objects(self):
        self.request();self.ticket.refresh_from_db()
        lease=ai.claim(self.ticket.pk,self.ticket.version)
        self.ticket.refresh_from_db()
        reply_ticket(self.owner,self.ticket.pk,{'version':self.ticket.version,'body':'请增加另一个对象 ZOTHER999'},[])
        self.ticket.refresh_from_db()
        following=ai.claim(self.ticket.pk,self.ticket.version)
        self.assertEqual(following['read_scope']['source_kinds']['ZDEMO001'],['fm'])
        self.assertNotIn('ZOTHER999',following['read_scope']['objects'])
        self.assertEqual(ai.renew(lease['run_id'],lease['lease_token'])['outcome'],'skipped')

    def test_unverified_retry_event_is_never_a_trigger(self):
        Event.objects.create(ticket=self.ticket,actor=self.admin,actor_name='系统',actor_role='system',
            kind='ai_retry',version=self.ticket.version)
        self.assertEqual(ai.trigger_for(self.ticket)[0].pk,self.old.trigger_id)
        self.assertEqual(ai.candidate_page()['results'],[])

    def test_new_round_uses_normal_evidence_and_notifies_exactly_once(self):
        old_before=TicketAIRun.objects.filter(pk=self.old.pk).values().get()
        self.request();self.ticket.refresh_from_db();lease=ai.claim(self.ticket.pk,self.ticket.version)
        auth={'run_id':lease['run_id'],'lease_token':lease['lease_token']}
        reserved=dispatch({'operation':'query_reserve',**auth,'request_key':str(uuid.uuid4()),
            'plan':{'command':'source','object':'ZDEMO001','kind':'fm'}})
        result={'system':'DS4','client':'110','object':'ZDEMO001','command':'source','sha256':'b'*64}
        receipt=dispatch({'operation':'query_complete',**auth,'query_id':reserved['query_id'],'result':result,'error':''})
        counts=(Notification.objects.count(),Delivery.objects.count())
        payload={'category':'query','body':'合成证据结论','evidence':[receipt['evidence']]}
        self.assertEqual(dispatch({'operation':'reply',**auth,'payload':payload})['outcome'],'replied')
        self.assertEqual(dispatch({'operation':'reply',**auth,'payload':payload})['outcome'],'already_replied')
        self.assertEqual((Notification.objects.count(),Delivery.objects.count()),(counts[0]+1,counts[1]+1))
        self.assertEqual(TicketAIRun.objects.filter(pk=self.old.pk).values().get(),old_before)
        self.assertEqual(ai.candidate_page()['results'],[])

    def test_human_reply_preempts_the_new_round(self):
        self.request();self.ticket.refresh_from_db()
        lease=ai.claim(self.ticket.pk,self.ticket.version)
        self.ticket.refresh_from_db()
        reply_ticket(self.admin,self.ticket.pk,{'version':self.ticket.version,'body':'人工已处理'},[])
        outcome=ai.publish(lease['run_id'],lease['lease_token'],{'category':'clarification','body':'请补充','evidence':[]})
        self.assertEqual(outcome['outcome'],'skipped')

    def test_untrusted_or_wrong_object_type_evidence_never_creates_a_round(self):
        valid=metadata()
        variants=[]
        x=copy.deepcopy(valid);x['_evidence']['system']='PS4';variants.append(x)
        x=copy.deepcopy(valid);x['_evidence']['contentSha256']='0'*64;variants.append(x)
        x=copy.deepcopy(valid);x['results']['prog']=[{'name':'ZDEMO001','subc':'1','text':'synthetic'}];x['_evidence']['contentSha256']=digest({'pattern':x['pattern'],'results':x['results']});variants.append(x)
        x=copy.deepcopy(valid);x['pattern']='ZOTHER999';x['_evidence']['contentSha256']=digest({'pattern':x['pattern'],'results':x['results']});variants.append(x)
        for evidence in variants:
            with self.subTest(evidence=evidence),self.assertRaises((PolicyError,ValidationError,ValueError)):
                self.request(proof=self.proof(evidence))
        self.assertEqual(TicketAIRun.objects.filter(ticket=self.ticket).count(),1)

    def test_existing_bound_source_type_cannot_be_replaced_by_catalogue_evidence(self):
        self.old.accepted_scope['source_kinds']['ZDEMO001']=['prog']
        self.old.save(update_fields=['accepted_scope'])
        with self.assertRaisesRegex(PolicyError,'source_kind_already_bound'):
            self.request()
        self.assertEqual(TicketAIRun.objects.filter(ticket=self.ticket).count(),1)

    def test_untyped_production_and_like_wildcard_names_remain_closed(self):
        scope=copy.deepcopy(self.old.accepted_scope)
        scope['system']='PS4';scope['client']='100'
        with self.assertRaisesRegex(PolicyError,'catalog_system_not_allowed'):
            validate_catalog_attestation(scope,'ZDEMO001','fm',metadata(system='PS4',client='100'))
        scope['system']='DS4';scope['client']='110';scope['objects']=['ZDEMO_01']
        with self.assertRaisesRegex(PolicyError,'catalog_object_not_exact'):
            validate_catalog_attestation(scope,'ZDEMO_01','fm',metadata(name='ZDEMO_01'))
        scope['objects']=['MARA']
        with self.assertRaisesRegex(PolicyError,'object_outside_scope'):
            validate_catalog_attestation(scope,'MARA','fm',metadata(name='MARA'))

    def test_stale_and_production_metadata_are_not_accepted(self):
        old=self.proof();old['observed_at']=(timezone.now()-timedelta(minutes=10)).isoformat()
        with self.assertRaises((PolicyError,ValidationError,ValueError)):self.request(proof=old)
        self.ticket.system='PS4';self.ticket.save(update_fields=['system'])
        with self.assertRaises((PolicyError,ValidationError,ValueError)):
            self.request(proof=self.proof(metadata(system='PS4',client='100')))

    def test_operator_command_is_not_in_the_automated_gateway(self):
        with self.assertRaises(PolicyError):
            dispatch({'operation':'continue','ticket_id':self.ticket.pk,'version':self.ticket.version})

    def test_management_command_accepts_only_explicit_validated_input(self):
        output=StringIO(); key=uuid.uuid4()
        with patch('desk.management.commands.ai_tickets.sys.stdin',StringIO(json.dumps(self.proof()))):
            call_command('ai_tickets','continue','--ticket-id',str(self.ticket.pk),
                         '--version',str(self.ticket.version),'--request-key',str(key),stdout=output)
        self.assertEqual(json.loads(output.getvalue())['outcome'],'queued')

@override_settings(AI_TICKET_AUTOMATION_ENABLED=True,DINGTALK_ENABLED=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SourceFailureDetailsTests(TestCase):
    def setUp(self):
        import sys
        from pathlib import Path
        scripts=str(Path(__file__).resolve().parents[2]/'scripts')
        if scripts not in sys.path:sys.path.insert(0,scripts)
        self.admin=User.objects.create_user(username='detail-admin@test.invalid',email='detail-admin@test.invalid',name='管理员',role='admin')
        self.owner=User.objects.create_user(username='detail-owner@test.invalid',email='detail-owner@test.invalid',name='顾问')
        # Untyped identifiers in the body are not automatic metadata targets.
        ticket=create_ticket(self.owner,{'request_key':uuid.uuid4(),'title':'接口排查','description':'ZDEMO001接口 synthetic',
            'system':'DS4','module':'MM','kind':'问题'},[])[0]
        self.ticket=change_ticket_status(self.admin,ticket.pk,{'version':ticket.version,'action':'start'})
        self.lease=ai.claim(self.ticket.pk,self.ticket.version)

    def test_missing_source_type_is_a_bounded_cause_without_a_query(self):
        from ai_ticket_worker import TicketProcessor
        def gateway(op, **values):return dispatch({'operation':op,**values})
        def planner(_context,_pulse):
            return {'action':'query','plan':{'command':'source','object':'ZDEMO001','kind':'fm',
                'method':'','fields':[],'filters':[],'rows':0},'body':'','evidence_ids':[],'reason':''}
        reader=__import__('unittest.mock',fromlist=['Mock']).Mock()
        result=TicketProcessor(gateway,planner,reader).process(self.lease)
        self.assertEqual(result['reason'],'authorization_missing')
        self.assertTrue(result['manual_required'])
        self.assertEqual(ai.ticket_ai_state(self.ticket)['state'],'manual_required')
        self.assertEqual(ai.candidate_page()['results'],[])
        audit=Audit.objects.filter(action='ai_ticket_blocked',target=str(self.ticket.pk)).latest('id')
        self.assertEqual(audit.detail['detail_code'],'source_type_missing')
        self.assertFalse(TicketAIQuery.objects.filter(run_id=self.lease['run_id']).exists())
        reader.assert_not_called()
        self.assertIn('需要确认对象源码类型',ai.ticket_ai_state(self.ticket)['message'])

    def test_exhausted_time_keeps_the_original_reason_and_detail_in_audit(self):
        run=TicketAIRun.objects.get(pk=self.lease['run_id'])
        run.deadline_at=timezone.now()+timedelta(minutes=6,seconds=30);run.save(update_fields=['deadline_at'])
        request={'operation':'defer','run_id':self.lease['run_id'],'lease_token':self.lease['lease_token'],
                 'reason':'authorization_missing','detail_code':'plan_outside_scope'}
        self.assertEqual(dispatch(request)['reason'],'time_limit')
        entry=Audit.objects.filter(action='ai_ticket_blocked',target=str(self.ticket.pk)).latest('id').detail
        self.assertEqual((entry['reason'],entry['terminal_reason'],entry['detail_code']),
                         ('authorization_missing','time_limit','plan_outside_scope'))
        self.assertIn('查询计划超出受理范围',ai.ticket_ai_state(self.ticket)['message'])

    def test_unrecognized_or_incompatible_detail_is_rejected(self):
        request={'operation':'defer','run_id':self.lease['run_id'],'lease_token':self.lease['lease_token'],
                 'reason':'authorization_missing','detail_code':'secret arbitrary text'}
        with self.assertRaises((PolicyError,ValidationError)):dispatch(request)
        self.assertFalse(Audit.objects.filter(action='ai_ticket_blocked',target=str(self.ticket.pk)).exists())
        with self.assertRaises((PolicyError,ValidationError)):
            dispatch({**request,'reason':'sap_unavailable','detail_code':'source_type_missing'})


@override_settings(AI_TICKET_AUTOMATION_ENABLED=True,DINGTALK_ENABLED=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ContinueConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_identical_operator_requests_create_only_one_round(self):
        admin=User.objects.create_user(username='race-admin@test.invalid',email='race-admin@test.invalid',name='管理员',role='admin')
        owner=User.objects.create_user(username='race-owner@test.invalid',email='race-owner@test.invalid',name='顾问')
        ticket=create_ticket(owner,{'request_key':uuid.uuid4(),'title':'ZDEMO001接口','description':'synthetic',
            'system':'DS4','module':'MM','kind':'问题'},[])[0]
        ticket=change_ticket_status(admin,ticket.pk,{'version':ticket.version,'action':'start'})
        lease=ai.claim(ticket.pk,ticket.version)
        prior=TicketAIRun.objects.get(pk=lease['run_id']);prior.status='blocked';prior.reason='time_limit'
        prior.attempts=3;prior.deadline_at=timezone.now()-timedelta(seconds=1);prior.save()
        proof={'object':'ZDEMO001','kind':'fm','observed_at':timezone.now().isoformat(),'evidence':metadata()}
        key=uuid.uuid4();barrier=Barrier(2);ticket_id=ticket.pk;version=ticket.version
        def request(_):
            try:
                barrier.wait(timeout=10)
                return ai.request_blocked_followup(ticket_id,version,key,proof)['outcome']
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(request,range(2)))
        self.assertCountEqual(results,['queued','already_requested'])
        self.assertEqual(TicketAIRun.objects.filter(ticket_id=ticket_id).count(),2)
        self.assertEqual(Event.objects.filter(ticket_id=ticket_id,kind='ai_retry').count(),1)
        prior.refresh_from_db();self.assertEqual((prior.attempts,prior.reason),(3,'time_limit'))

    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_distinct_operator_requests_do_not_create_two_rounds(self):
        admin=User.objects.create_user(username='race2-admin@test.invalid',email='race2-admin@test.invalid',name='管理员',role='admin')
        owner=User.objects.create_user(username='race2-owner@test.invalid',email='race2-owner@test.invalid',name='顾问')
        ticket=create_ticket(owner,{'request_key':uuid.uuid4(),'title':'ZDEMO001接口','description':'synthetic',
            'system':'DS4','module':'MM','kind':'问题'},[])[0]
        ticket=change_ticket_status(admin,ticket.pk,{'version':ticket.version,'action':'start'})
        lease=ai.claim(ticket.pk,ticket.version)
        prior=TicketAIRun.objects.get(pk=lease['run_id']);prior.status='blocked';prior.reason='time_limit'
        prior.attempts=3;prior.deadline_at=timezone.now()-timedelta(seconds=1);prior.save()
        proof={'object':'ZDEMO001','kind':'fm','observed_at':timezone.now().isoformat(),'evidence':metadata()}
        barrier=Barrier(2);ticket_id=ticket.pk;version=ticket.version
        def request(_):
            try:
                barrier.wait(timeout=10)
                value=ai.request_blocked_followup(ticket_id,version,uuid.uuid4(),proof)
                return value['outcome'] if value['outcome']=='queued' else value['reason']
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(request,range(2)))
        self.assertCountEqual(results,['queued','version_changed'])
        self.assertEqual(TicketAIRun.objects.filter(ticket_id=ticket_id).count(),2)
        self.assertEqual(Event.objects.filter(ticket_id=ticket_id,kind='ai_retry').count(),1)
