"""Fixed operations for a trusted coordinator. No HTTP, model or SAP execution."""
import re
import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import ai
from .ai_policy import MAX_QUERIES, QUERY_SECONDS, PolicyError, digest, exact_keys, validate_plan
from .models import Audit, Ticket, TicketAIRun, TicketAIQuery

OPERATIONS = {'list', 'claim', 'renew', 'reply', 'defer', 'query_reserve', 'query_complete'}
QUERY_ERRORS = {'sap_unavailable', 'authorization_missing', 'runtime_error', 'time_limit', 'evidence_incomplete'}


def live_run(run_id, token):
    """Called inside atomic(); retain the existing ticket-before-run lock order."""
    parent = TicketAIRun.objects.get(pk=run_id)
    ticket = Ticket.objects.select_for_update().get(pk=parent.ticket_id)
    run = TicketAIRun.objects.select_for_update().get(pk=run_id)
    trigger, accepted, reason = ai.trigger_for(ticket)
    now = timezone.now()
    if (reason or str(run.lease_token) != str(token) or run.status != 'running'
            or run.lease_expires_at <= now or not run.deadline_at or run.deadline_at <= now
            or ticket.version != run.context_version or trigger.pk != run.trigger_id
            or accepted.pk != run.accepted_event_id or ticket.system != run.accepted_scope.get('system')):
        raise PolicyError('run_not_live')
    return ticket, run


def reserve(run_id, token, request_key, plan):
    with transaction.atomic():
        ticket, run = live_run(run_id, token)
        clean = validate_plan(plan, run.accepted_scope)
        existing = run.queries.filter(request_key=request_key).first()
        if existing:
            if existing.plan_hash != digest(clean): raise PolicyError('request_key_reused')
            # Even a timed-out transport cannot authorize a second SAP invocation.
            return {'outcome': 'already_reserved', 'query_id': str(existing.pk), 'status': existing.status}
        if run.queries.count() >= MAX_QUERIES: raise PolicyError('query_limit')
        now = timezone.now()
        if run.deadline_at - now < timedelta(minutes=5): raise PolicyError('time_limit')
        query = TicketAIQuery.objects.create(run=run, request_key=request_key,
            context_version=run.context_version, lease_token=run.lease_token,
            plan=clean, plan_hash=digest(clean), deadline_at=min(now + timedelta(seconds=QUERY_SECONDS), run.lease_expires_at))
        Audit.objects.create(actor=run.accepted_event.actor, action='ai_ticket_query_reserved', target=str(ticket.pk),
            detail={'run_id': str(run.pk), 'query_id': str(query.pk), 'plan_hash': query.plan_hash})
        return {'outcome': 'reserved', 'query_id': str(query.pk), 'plan': clean,
            'scope': run.accepted_scope, 'deadline_at': query.deadline_at.isoformat(), 'timeout_seconds': QUERY_SECONDS}


def complete(run_id, token, query_id, result, error):
    if error and error not in QUERY_ERRORS: raise PolicyError('invalid_error')
    if bool(error) == bool(result): raise PolicyError('invalid_completion')
    with transaction.atomic():
        ticket, run = live_run(run_id, token)
        query = TicketAIQuery.objects.select_for_update().get(pk=query_id, run=run)
        if query.context_version != run.context_version or query.lease_token != run.lease_token:
            raise PolicyError('query_lease_changed')
        if query.status != 'reserved': raise PolicyError('query_already_completed')
        if query.deadline_at <= timezone.now():
            error, result = 'time_limit', None
        if result:
            exact_keys(result, {'system', 'client', 'object', 'command', 'sha256'})
            if (result['system'] != run.accepted_scope['system'] or result['client'] != run.accepted_scope['client']
                    or result['object'] != query.plan['object'] or result['command'] != query.plan['command']
                    or not isinstance(result['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', result['sha256'])):
                raise PolicyError('evidence_mismatch')
        query.status = 'failed' if error else 'succeeded'
        query.error_code = error
        query.result_hash = result['sha256'] if result else ''
        query.finished_at = timezone.now()
        query.save(update_fields=['status', 'error_code', 'result_hash', 'finished_at'])
        Audit.objects.create(actor=run.accepted_event.actor, action='ai_ticket_query_completed', target=str(ticket.pk),
            detail={'run_id': str(run.pk), 'query_id': str(query.pk), 'status': query.status, 'result_hash': query.result_hash})
        if error: return {'outcome': 'failed', 'reason': error}
        return {'outcome': 'recorded', 'collected_at': query.finished_at.isoformat(), 'evidence': {'system': result['system'], 'client': result['client'],
            'reference': 'sap-ticket-query:' + str(query.pk)}}


def dispatch(request):
    exact_keys(request, {'operation'}, {'after_id', 'limit', 'ticket_id', 'version', 'run_id', 'lease_token',
        'payload', 'reason', 'request_key', 'plan', 'query_id', 'result', 'error'})
    operation = request['operation']
    if not isinstance(operation, str) or operation not in OPERATIONS: raise PolicyError('operation_not_allowed')
    fields = {
        'list': ({'operation'}, {'after_id', 'limit'}),
        'claim': ({'operation', 'ticket_id', 'version'}, set()),
        'renew': ({'operation', 'run_id', 'lease_token'}, set()),
        'reply': ({'operation', 'run_id', 'lease_token', 'payload'}, set()),
        'defer': ({'operation', 'run_id', 'lease_token', 'reason'}, set()),
        'query_reserve': ({'operation', 'run_id', 'lease_token', 'request_key', 'plan'}, set()),
        'query_complete': ({'operation', 'run_id', 'lease_token', 'query_id', 'result', 'error'}, set()),
    }
    exact_keys(request, *fields[operation])
    if operation == 'list':
        after, limit = request.get('after_id', 0), request.get('limit', 10)
        if type(after) is not int or type(limit) is not int: raise PolicyError('invalid_pagination')
        result = ai.candidate_page(after, limit)
        result['results'] = [{key: item[key] for key in ['id', 'number', 'version']} for item in result['results']]
        return result
    if operation == 'claim':
        if any(type(request[key]) is not int or request[key] < 1 for key in ['ticket_id', 'version']):
            raise PolicyError('invalid_claim')
        return ai.claim(request['ticket_id'], request['version'])
    run_id, token = uuid.UUID(request['run_id']), uuid.UUID(request['lease_token'])
    if operation == 'renew': return ai.renew(run_id, token)
    if operation == 'reply': return ai.publish(run_id, token, request['payload'])
    if operation == 'defer': return ai.defer(run_id, token, request['reason'])
    if operation == 'query_reserve':
        return reserve(run_id, token, uuid.UUID(request['request_key']), request['plan'])
    return complete(run_id, token, uuid.UUID(request['query_id']), request['result'], request['error'])
