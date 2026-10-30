"""Fixed operations for a trusted coordinator. No HTTP, model or SAP execution."""
import re
import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import ai
from .ai_policy import (MAX_QUERIES, QUERY_SECONDS, SOURCE_TYPE_TABLES, PolicyError, digest, exact_keys,
    freeze_scope, source_type_cli_arguments, validate_plan)
from .models import Audit, Ticket, TicketAIRun, TicketAIQuery

OPERATIONS = {'list', 'claim', 'renew', 'reply', 'defer', 'query_reserve', 'source_type_reserve', 'query_complete'}
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
        return reserve_query(ticket, run, request_key, clean)


def reserve_query(ticket, run, request_key, clean):
    """Caller holds the live ticket/run locks; all queries share these gates."""
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


def source_type_reserve(run_id, token, request_key, obj):
    with transaction.atomic():
        ticket, run = live_run(run_id, token)
        original = freeze_scope(ticket.system, ticket.title, ticket.description, run.accepted_event.body, run.accepted_event_id)
        if (original['source_hash'] != run.accepted_scope.get('source_hash') or original['restriction_unresolved']
                or obj not in original['source_type_objects']):
            raise PolicyError('accepted_material_mismatch')
        # Independently validate the fixed request before choosing any lookup.
        source_type_cli_arguments({'command': 'source-type', 'object': obj, 'table': 'DD02L'}, run.accepted_scope)
        existing = run.queries.filter(request_key=request_key).first()
        if existing:
            if existing.plan.get('command') != 'source-type' or existing.plan.get('object') != obj:
                raise PolicyError('request_key_reused')
            return {'outcome': 'already_reserved', 'query_id': str(existing.pk), 'status': existing.status}
        if run.accepted_scope.get('source_kinds', {}).get(obj):
            return {'outcome': 'source_type_checked', 'binding': 'bound', 'read_scope': run.accepted_scope,
                'queries_used': run.queries.count()}
        checks = list(run.queries.filter(plan__command='source-type', plan__object=obj).order_by('created_at', 'id'))
        checked = {query.plan['table'] for query in checks}
        if (len(checked) != len(checks) or any(query.status != 'succeeded' or query.lease_token != run.lease_token
                or query.finished_at is None or query.finished_at < timezone.now() - timedelta(minutes=5) for query in checks)):
            return {'outcome': 'source_type_unavailable', 'reason': 'evidence_incomplete'}
        remaining = [table for table in SOURCE_TYPE_TABLES if table not in checked]
        # Retain capacity for at least the actual source read after metadata.
        if not remaining or run.queries.count() + len(remaining) + 1 > MAX_QUERIES:
            return {'outcome': 'source_type_unavailable', 'reason': 'query_limit'}
        return reserve_query(ticket, run, request_key, {'command': 'source-type', 'object': obj, 'table': remaining[0]})


def source_type_binding(ticket, run, obj):
    """Only completed executor receipts may fill a missing type. No model proof."""
    original = freeze_scope(ticket.system, ticket.title, ticket.description, run.accepted_event.body, run.accepted_event_id)
    if (original['source_hash'] != run.accepted_scope.get('source_hash') or original['restriction_unresolved']
            or obj not in original['source_type_objects']):
        return 'unresolved'
    checks = list(run.queries.filter(plan__command='source-type', plan__object=obj).order_by('created_at', 'id'))
    if len(checks) < len(SOURCE_TYPE_TABLES): return 'pending'
    if (len(checks) != len(SOURCE_TYPE_TABLES) or {query.plan['table'] for query in checks} != set(SOURCE_TYPE_TABLES)
            or any(query.status != 'succeeded' or query.context_version != run.context_version
                or query.lease_token != run.lease_token or not query.result_hash or not query.finished_at
                or query.finished_at < timezone.now() - timedelta(minutes=5) for query in checks)):
        return 'unresolved'
    matches = []
    for query in checks:
        audit = Audit.objects.filter(action='ai_ticket_query_completed', target=str(ticket.pk),
            detail__run_id=str(run.pk), detail__query_id=str(query.pk), detail__result_hash=query.result_hash).first()
        if not audit or not isinstance(audit.detail.get('source_type_matches'), list): return 'unresolved'
        matches += audit.detail['source_type_matches']
    if not matches: return 'unresolved'
    if len(matches) != 1 or matches[0] not in {'fm', 'prog', 'class'}: return 'ambiguous'
    if run.accepted_scope.get('source_kinds', {}).get(obj): return 'unresolved'
    run.accepted_scope['source_kinds'][obj] = matches
    run.accepted_scope.setdefault('source_kind_bindings', {})[obj] = {
        'source': 'fixed_metadata_reads', 'accepted_event_id': run.accepted_event_id,
        'query_ids': [str(query.pk) for query in checks], 'result_hashes': [query.result_hash for query in checks],
        'bound_at': timezone.now().isoformat()}
    run.save(update_fields=['accepted_scope'])
    Audit.objects.create(actor=run.accepted_event.actor, action='ai_ticket_source_type_bound', target=str(ticket.pk),
        detail={'run_id': str(run.pk), 'object': obj, 'kind': matches[0],
            'query_ids': [str(query.pk) for query in checks], 'source_hash': run.accepted_scope['source_hash']})
    return 'bound'


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
        metadata_read = query.plan.get('command') == 'source-type'
        if result:
            exact_keys(result, {'system', 'client', 'object', 'command', 'sha256'} |
                ({'source_type_matches'} if metadata_read else set()))
            if (result['system'] != run.accepted_scope['system'] or result['client'] != run.accepted_scope['client']
                    or result['object'] != query.plan['object'] or result['command'] != query.plan['command']
                    or not isinstance(result['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', result['sha256'])):
                raise PolicyError('evidence_mismatch')
            if metadata_read:
                source_type_cli_arguments(query.plan, run.accepted_scope)
                matches = result['source_type_matches']
                kinds = {SOURCE_TYPE_TABLES[query.plan['table']][2]}
                if query.plan['table'] == 'SEOCLASS': kinds.add('interface')
                if (not isinstance(matches, list) or len(matches) > 2
                        or any(not isinstance(kind, str) or kind not in kinds for kind in matches)):
                    raise PolicyError('metadata_evidence_invalid')
        query.status = 'failed' if error else 'succeeded'
        query.error_code = error
        query.result_hash = result['sha256'] if result else ''
        query.finished_at = timezone.now()
        query.save(update_fields=['status', 'error_code', 'result_hash', 'finished_at'])
        detail = {'run_id': str(run.pk), 'query_id': str(query.pk), 'status': query.status, 'result_hash': query.result_hash}
        if metadata_read and result: detail['source_type_matches'] = result['source_type_matches']
        Audit.objects.create(actor=run.accepted_event.actor, action='ai_ticket_query_completed', target=str(ticket.pk), detail=detail)
        if error: return {'outcome': 'failed', 'reason': error}
        if metadata_read:
            return {'outcome': 'source_type_checked', 'binding': source_type_binding(ticket, run, query.plan['object']),
                'read_scope': run.accepted_scope, 'queries_used': run.queries.count()}
        return {'outcome': 'recorded', 'collected_at': query.finished_at.isoformat(), 'evidence': {'system': result['system'], 'client': result['client'],
            'reference': 'sap-ticket-query:' + str(query.pk)}}


def dispatch(request):
    exact_keys(request, {'operation'}, {'after_id', 'limit', 'ticket_id', 'version', 'run_id', 'lease_token',
        'payload', 'reason', 'detail_code', 'request_key', 'plan', 'query_id', 'result', 'error', 'object'})
    operation = request['operation']
    if not isinstance(operation, str) or operation not in OPERATIONS: raise PolicyError('operation_not_allowed')
    fields = {
        'list': ({'operation'}, {'after_id', 'limit'}),
        'claim': ({'operation', 'ticket_id', 'version'}, set()),
        'renew': ({'operation', 'run_id', 'lease_token'}, set()),
        'reply': ({'operation', 'run_id', 'lease_token', 'payload'}, set()),
        'defer': ({'operation', 'run_id', 'lease_token', 'reason'}, {'detail_code'}),
        'query_reserve': ({'operation', 'run_id', 'lease_token', 'request_key', 'plan'}, set()),
        'source_type_reserve': ({'operation', 'run_id', 'lease_token', 'request_key', 'object'}, set()),
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
    if operation == 'renew':
        result = ai.renew(run_id, token)
        if result.get('outcome') == 'renewed':
            from .ai_monitor import touch_worker_heartbeat
            touch_worker_heartbeat()
        return result
    if operation == 'reply': return ai.publish(run_id, token, request['payload'])
    if operation == 'defer': return ai.defer(run_id, token, request['reason'], request.get('detail_code', ''))
    if operation == 'query_reserve':
        return reserve(run_id, token, uuid.UUID(request['request_key']), request['plan'])
    if operation == 'source_type_reserve':
        return source_type_reserve(run_id, token, uuid.UUID(request['request_key']), request['object'])
    return complete(run_id, token, uuid.UUID(request['query_id']), request['result'], request['error'])
