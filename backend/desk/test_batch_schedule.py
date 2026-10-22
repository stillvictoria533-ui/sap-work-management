import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .ai import claim, publish
from .dingtalk import DingTalkClient
from .models import User, Ticket, Event, ExpediteRequest, ScheduleBatch, Delivery, Notification, TicketAIRun
from .scheduling import commit_batch
from .scheduling_serializers import BatchScheduleInput
from .services import change_ticket_status


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   DINGTALK_ENABLED=False, AI_TICKET_AUTOMATION_ENABLED=False)
class BatchScheduleTests(TestCase):
    def setUp(self):
        self.admin = self.user('admin', 'admin')
        self.alice = self.user('alice')
        self.bob = self.user('bob')
        self.client = self.login(self.admin)
        self.alice_client = self.login(self.alice)
        self.bob_client = self.login(self.bob)
        self.today = timezone.localdate()

    def user(self, name, role='consultant'):
        return User.objects.create_user(username=name, email=name+'@batch.test', name=name,
                                        role=role, must_change_password=False)

    def login(self, user):
        client = APIClient()
        client.force_login(user)
        session = client.session
        session['version'] = user.session_version
        session.save()
        return client

    def ticket(self, days=5, **values):
        return Ticket.objects.create(**{'owner': self.alice, 'title': '批量排期样例', 'module': 'MM',
            'system': 'DS4', 'kind': '开发', 'description': '本地测试，不涉及 SAP', 'status': 'in-progress',
            'estimated_completion_date': self.today+timedelta(days=days), **values})

    def expedite(self, ticket, **extra):
        ticket.refresh_from_db()
        data = {'request_key': str(uuid.uuid4()), 'version': ticket.version,
                'requested_date': str(self.today+timedelta(days=2)), 'reason': '需要提前上线，秘密业务影响', **extra}
        response = self.alice_client.post(f'/api/tickets/{ticket.pk}/expedite', data, format='json')
        return response, data

    def change(self, ticket, days):
        ticket.refresh_from_db()
        return {'ticket_id': ticket.pk, 'version': ticket.version,
                'estimated_completion_date': str(self.today+timedelta(days=days)) if days is not None else None}

    def batch(self, changes, **extra):
        return {'request_key': str(uuid.uuid4()), 'changes': changes, 'reason': '根据业务优先级统一调整开发排期', **extra}

    def preview(self, data):
        return self.client.post('/api/scheduling/preview', data, format='json')

    def commit(self, data):
        preview = self.preview(data)
        self.assertEqual(preview.status_code, 200, preview.data)
        payload = {**data, 'preview_token': preview.data['preview_token']}
        return self.client.post('/api/scheduling/commit', payload, format='json'), payload

    def test_expedite_records_request_not_change_plan_and_is_idempotent(self):
        ticket = self.ticket()
        before = (ticket.status, ticket.priority, ticket.estimated_completion_date)
        response, data = self.expedite(ticket)
        self.assertEqual(response.status_code, 201, response.data)
        ticket.refresh_from_db()
        self.assertEqual((ticket.status, ticket.priority, ticket.estimated_completion_date), before)
        self.assertEqual(ticket.version, 2)
        self.assertEqual(response.data['expedite_requests'][0]['status'], 'pending')
        self.assertEqual(Notification.objects.get().recipient, self.admin)
        self.assertEqual(Delivery.objects.get().channel, 'group_admin')
        repeated = self.alice_client.post(f'/api/tickets/{ticket.pk}/expedite', data, format='json')
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(ExpediteRequest.objects.count(), 1)
        self.assertEqual(Event.objects.count(), 1)
        duplicate, _ = self.expedite(ticket)
        self.assertEqual(duplicate.status_code, 400)
        forged = self.alice_client.post(f'/api/tickets/{ticket.pk}/expedite', {**data, 'reason':'不同内容'}, format='json')
        self.assertEqual(forged.status_code, 409)

    def test_request_permissions_dates_and_closed_states(self):
        ticket = self.ticket()
        _, data = self.expedite(ticket)
        self.assertEqual(self.bob_client.post(f'/api/tickets/{ticket.pk}/expedite', data, format='json').status_code, 404)
        self.assertEqual(self.client.post(f'/api/tickets/{ticket.pk}/expedite', data, format='json').status_code, 403)
        for values in [{'requested_date':str(self.today-timedelta(days=1))},
                       {'requested_date':str(ticket.estimated_completion_date)}, {'reason':'   '}]:
            other = self.ticket()
            response, _ = self.expedite(other, **values)
            self.assertEqual(response.status_code, 400)
        for state in ['waiting', 'done']:
            other = self.ticket(status=state)
            self.assertEqual(self.expedite(other)[0].status_code, 400)
        unplanned = self.ticket(estimated_completion_date=None)
        self.assertEqual(self.expedite(unplanned)[0].status_code, 201)

    def test_request_visibility_and_admin_only_queue_and_batch(self):
        ticket = self.ticket()
        self.expedite(ticket)
        request = ExpediteRequest.objects.get()
        self.assertEqual(self.bob_client.get('/api/expedite-requests').data['count'], 0)
        self.assertEqual(self.bob_client.get(f'/api/expedite-requests/{request.pk}').status_code, 404)
        for path in ['/api/scheduling/queue']:
            self.assertEqual(self.alice_client.get(path).status_code, 403)
        for path in ['/api/scheduling/preview', '/api/scheduling/commit', f'/api/expedite-requests/{request.pk}/decision']:
            self.assertEqual(self.alice_client.post(path, {}, format='json').status_code, 403)
        queue = self.client.get('/api/scheduling/queue').data
        self.assertEqual(queue['results'][0]['pending_expedite']['id'], request.pk)
        self.assertEqual(self.client.get('/api/stats').data['expedite_pending'], 1)
        self.assertEqual(self.alice_client.get('/api/stats').data['expedite_pending'], 0)

    def test_batch_previews_then_moves_only_selected_rows_and_fulfills_request(self):
        a = self.ticket(1)
        b = self.ticket(2)
        c = self.ticket(4, owner=self.bob)
        d = self.ticket(5)
        e = self.ticket(6)
        self.expedite(d)
        request = ExpediteRequest.objects.get()
        data = self.batch([self.change(d,2), self.change(b,4), self.change(c,5)],
                          expedite_request_id=request.pk, expedite_request_version=request.version)
        before = Event.objects.count()
        preview = self.preview(data)
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual((preview.data['advanced_count'], preview.data['delayed_count'], preview.data['recipient_count']), (1,2,2))
        self.assertEqual(Event.objects.count(), before)
        self.assertEqual(ScheduleBatch.objects.count(), 0)
        d.refresh_from_db()
        self.assertEqual(d.estimated_completion_date, self.today+timedelta(days=5))
        result, _ = self.commit(data)
        self.assertEqual(result.status_code, 200, result.data)
        for t, days in [(a,1),(b,4),(c,5),(d,2),(e,6)]:
            t.refresh_from_db()
            self.assertEqual(t.estimated_completion_date, self.today+timedelta(days=days))
            self.assertEqual(t.status, 'in-progress')
        request.refresh_from_db()
        self.assertEqual(request.status, 'approved')
        self.assertEqual(ScheduleBatch.objects.get().source_request, request)
        batch = ScheduleBatch.objects.get()
        self.assertEqual(batch.events.count(), 3)
        self.assertEqual(Delivery.objects.filter(schedule_batch=batch).count(), 2)
        self.assertEqual(Notification.objects.filter(event__schedule_batch=batch).count(), 3)
        self.assertFalse(Event.objects.filter(ticket__in=[a,e]).exists())
        bob_notices = self.bob_client.get('/api/notifications').data['results']
        self.assertEqual(len(bob_notices), 1)
        self.assertIn(str(self.today+timedelta(days=5)), bob_notices[0]['body'])
        self.assertNotIn('秘密业务影响', bob_notices[0]['body'])
        self.assertEqual(self.bob_client.get(f'/api/tickets/{d.pk}').status_code, 404)

    def test_replayed_batch_does_not_duplicate_events_or_notifications(self):
        ticket = self.ticket()
        data = self.batch([self.change(ticket,3)])
        result, payload = self.commit(data)
        self.assertEqual(result.status_code, 200)
        again = self.client.post('/api/scheduling/commit', payload, format='json')
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.data, result.data)
        self.assertEqual((ScheduleBatch.objects.count(), Event.objects.count(), Notification.objects.count(), Delivery.objects.count()), (1,1,1,1))
        altered = self.client.post('/api/scheduling/commit', {**payload,'reason':'改变内容'}, format='json')
        self.assertEqual(altered.status_code, 409)

    def test_preview_cannot_be_skipped_modified_or_reused_by_another_admin(self):
        ticket = self.ticket()
        data = self.batch([self.change(ticket,3)])
        self.assertEqual(self.client.post('/api/scheduling/commit', data, format='json').status_code, 400)
        preview = self.preview(data)
        proof = preview.data['preview_token']
        changed = self.client.post('/api/scheduling/commit', {**data,'reason':'内容被修改','preview_token':proof}, format='json')
        self.assertEqual(changed.status_code, 400)
        other = self.login(self.user('other-admin','admin'))
        self.assertEqual(other.post('/api/scheduling/commit', {**data,'preview_token':proof}, format='json').status_code, 400)
        self.assertEqual(ScheduleBatch.objects.count(), 0)

    def test_stale_ticket_or_request_aborts_whole_batch(self):
        a,b = self.ticket(),self.ticket(owner=self.bob)
        data = self.batch([self.change(a,3),self.change(b,4)])
        preview = self.preview(data)
        Ticket.objects.filter(pk=b.pk).update(version=2)
        result = self.client.post('/api/scheduling/commit', {**data,'preview_token':preview.data['preview_token']}, format='json')
        self.assertEqual(result.status_code, 409)
        a.refresh_from_db()
        self.assertEqual(a.estimated_completion_date, self.today+timedelta(days=5))
        self.assertEqual(ScheduleBatch.objects.count(), 0)
        self.assertEqual(Event.objects.count(), 0)

    def test_validation_for_closed_duplicate_missing_noop_and_dates(self):
        ticket = self.ticket()
        for rows in [[self.change(ticket,5)], [self.change(ticket,3)]*2,
                     [self.change(ticket,-1)], [{'ticket_id':99999,'version':1,'estimated_completion_date':str(self.today)}]]:
            self.assertEqual(self.preview(self.batch(rows)).status_code, 400)
        for status in ['waiting','done']:
            closed = self.ticket(status=status)
            self.assertEqual(self.preview(self.batch([self.change(closed,3)])).status_code, 400)
        self.assertEqual(ScheduleBatch.objects.count(), 0)
        self.assertEqual(self.client.get('/api/scheduling/queue').data['count'], 1)

    def test_request_can_only_be_approved_with_satisfactory_date(self):
        ticket = self.ticket()
        self.expedite(ticket)
        request = ExpediteRequest.objects.get()
        fields = {'expedite_request_id':request.pk,'expedite_request_version':request.version}
        self.assertEqual(self.preview(self.batch([self.change(ticket,3)], **fields)).status_code, 400)
        self.assertEqual(self.preview(self.batch([self.change(ticket,None)], **fields)).status_code, 400)
        fields['expedite_request_version'] = 999
        self.assertEqual(self.preview(self.batch([self.change(ticket,2)], **fields)).status_code, 409)
        request.refresh_from_db()
        self.assertEqual(request.status, 'pending')

    def test_failure_after_updates_rolls_back_all_changes(self):
        ticket = self.ticket()
        self.expedite(ticket)
        request = ExpediteRequest.objects.get()
        data = self.batch([self.change(ticket,2)], expedite_request_id=request.pk, expedite_request_version=request.version)
        proof = self.preview(data).data['preview_token']
        parsed = BatchScheduleInput(data={**data,'preview_token':proof})
        parsed.is_valid(raise_exception=True)
        counts=(Event.objects.count(),Notification.objects.count(),Delivery.objects.count())
        with patch('desk.scheduling.notify_batch',side_effect=RuntimeError('transaction test')):
            with self.assertRaises(RuntimeError):
                commit_batch(self.admin, parsed.validated_data)
        ticket.refresh_from_db();request.refresh_from_db()
        self.assertEqual(ticket.estimated_completion_date, self.today+timedelta(days=5))
        self.assertEqual(request.status, 'pending')
        self.assertEqual(ScheduleBatch.objects.count(), 0)
        self.assertEqual((Event.objects.count(),Notification.objects.count(),Delivery.objects.count()),counts)

    def test_rejection_requires_reason_preserves_schedule_and_notifies_requester(self):
        ticket = self.ticket()
        self.expedite(ticket)
        request = ExpediteRequest.objects.get();ticket.refresh_from_db()
        url=f'/api/expedite-requests/{request.pk}/decision'
        payload={'version':request.version,'ticket_version':ticket.version,'reason':'当前承诺交付无法提前，已与业务沟通'}
        self.assertEqual(self.client.post(url,{**payload,'reason':''},format='json').status_code,400)
        response=self.client.post(url,payload,format='json')
        self.assertEqual(response.status_code,200,response.data)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estimated_completion_date,self.today+timedelta(days=5))
        self.assertEqual(response.data['status'],'rejected')
        self.assertEqual(Notification.objects.filter(recipient=self.alice).count(),1)
        self.assertEqual(self.client.post(url,payload,format='json').status_code,409)

    def test_existing_single_date_edit_and_status_changes_reconcile_requests(self):
        ticket=self.ticket();self.expedite(ticket);ticket.refresh_from_db()
        self.assertEqual(self.client.post(f'/api/tickets/{ticket.pk}/schedule',{
            'version':ticket.version,'estimated_completion_date':str(self.today+timedelta(days=2)),
            'reason':'单独调整已满足申请日期'},format='json').status_code,200)
        self.assertEqual(ExpediteRequest.objects.get(ticket=ticket).status,'approved')
        other=self.ticket();self.expedite(other);other.refresh_from_db()
        change_ticket_status(self.admin,other.pk,{'action':'submit','version':other.version,'reason':'已完成开发，提交验收'})
        self.assertEqual(ExpediteRequest.objects.get(ticket=other).status,'cancelled')
        self.assertEqual(self.client.get('/api/stats').data['expedite_pending'],0)

    @override_settings(DINGTALK_ENABLED=True,PUBLIC_BASE_URL='https://desk.example.com',
                       DINGTALK_GROUP_TOKEN='test-token',DINGTALK_GROUP_SECRET='test-secret')
    def test_dingtalk_combines_same_owner_without_leaking_other_tickets(self):
        self.alice.dingtalk_mobile='13800000001';self.alice.save()
        self.bob.dingtalk_mobile='13800000002';self.bob.save()
        a,b,c=self.ticket(),self.ticket(),self.ticket(owner=self.bob)
        response,_=self.commit(self.batch([self.change(a,2),self.change(b,3),self.change(c,4)]))
        self.assertEqual(response.status_code,200)
        jobs=Delivery.objects.select_related('event__ticket','recipient','schedule_batch').order_by('recipient_id')
        self.assertEqual(jobs.count(),2)
        client=DingTalkClient()
        with patch.object(client,'post',return_value={'errcode':0}) as send:
            client.send(jobs.get(recipient=self.alice))
            text=send.call_args.kwargs['json']['text']['content']
            self.assertIn('2 张工单排期有调整',text)
            self.assertIn('/#/notifications',text)
            self.assertNotIn(a.title,text)
            self.assertNotIn('13800000002',text)

    @override_settings(AI_TICKET_AUTOMATION_ENABLED=True)
    def test_expedite_invalidates_old_ai_lease_without_new_round_or_budget(self):
        ticket=self.ticket(kind='问题',status='pending')
        change_ticket_status(self.admin,ticket.pk,{'version':ticket.version,'action':'start'})
        ticket.refresh_from_db();lease=claim(ticket.pk,ticket.version)
        self.assertEqual(lease['outcome'],'claimed')
        run=TicketAIRun.objects.get();deadline=run.deadline_at;attempts=run.attempts
        self.expedite(ticket)
        run.refresh_from_db()
        self.assertEqual((run.deadline_at,run.attempts),(deadline,attempts))
        result=publish(lease['run_id'],lease['lease_token'],{'category':'clarification','body':'需要补充信息','evidence':[]})
        self.assertEqual(result['reason'],'lease_changed')
        self.assertEqual(TicketAIRun.objects.count(),1)

    def test_expired_preview_does_not_write_a_batch(self):
        import time
        ticket = self.ticket()
        data = self.batch([self.change(ticket,3)])
        proof = self.preview(data).data['preview_token']
        expired_time = time.time() + 1000
        with patch('django.core.signing.time.time', return_value=expired_time):
            response = self.client.post('/api/scheduling/commit', {**data,'preview_token':proof}, format='json')
        self.assertEqual(response.status_code,400)
        self.assertEqual(ScheduleBatch.objects.count(),0)

    def test_calendar_and_owner_notices_reflect_batch_and_inactive_owner_is_not_notified(self):
        a=self.ticket();b=self.ticket(owner=self.bob)
        self.bob.is_active=False;self.bob.save()
        response,_=self.commit(self.batch([self.change(a,1),self.change(b,None)]))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['recipient_count'],1)
        self.assertEqual(response.data['cleared_count'],1)
        self.assertEqual(Notification.objects.count(),1)
        selected=str(self.today+timedelta(days=1))
        calendar=self.client.get('/api/calendar',{'mode':'estimated','month':selected[:7],'date':selected})
        self.assertEqual(calendar.status_code,200)
        self.assertEqual([t['id'] for t in calendar.data['results']],[a.pk])

    @override_settings(DINGTALK_ENABLED=True,PUBLIC_BASE_URL='https://desk.example.com',
                       DINGTALK_GROUP_TOKEN='test-token',DINGTALK_GROUP_SECRET='test-secret')
    def test_expedite_dingtalk_announces_request_without_business_reason(self):
        self.admin.dingtalk_mobile='13800000003';self.admin.save()
        ticket=self.ticket();self.expedite(ticket)
        job=Delivery.objects.select_related('event__ticket').get(channel='group_admin')
        client=DingTalkClient()
        with patch.object(client,'post',return_value={'errcode':0}) as send:
            client.send(job)
            text=send.call_args.kwargs['json']['text']['content']
            self.assertIn('加急申请',text)
            self.assertNotIn('秘密业务影响',text)
            self.assertNotIn(ticket.title,text)
