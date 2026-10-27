"""Local trusted-executor fixtures; never imported by runtime code."""
import uuid
from datetime import timedelta
from django.utils import timezone
from .ai_policy import digest
from .models import TicketAIRun, TicketAIQuery


def receipt_payload(lease, body='测试回答：依据本地夹具证据说明。'):
    run = TicketAIRun.objects.get(pk=lease['run_id'])
    plan = {'command': 'ddic', 'object': 'MARA'}
    query, _ = TicketAIQuery.objects.get_or_create(run=run,
        request_key=uuid.uuid5(uuid.NAMESPACE_URL, str(lease['lease_token'])), defaults={
            'context_version': run.context_version, 'lease_token': lease['lease_token'],
            'plan': plan, 'plan_hash': digest(plan), 'status': 'succeeded',
            'deadline_at': timezone.now() + timedelta(seconds=60),
            'result_hash': 'a' * 64, 'finished_at': timezone.now()})
    system = run.accepted_scope['system']
    return {'category': 'query', 'body': body, 'evidence': [{
        'system': system, 'client': run.accepted_scope['client'], 'reference': 'sap-ticket-query:' + str(query.pk)}]}
