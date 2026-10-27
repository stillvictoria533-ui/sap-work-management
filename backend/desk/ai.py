"""Accepted-ticket AI follow-up gateway; invoked only by a trusted management command.

No model or SAP call is made here. Ticket/SAP text remains untrusted evidence.
"""
import uuid
import copy
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

from .models import Audit, Event, Ticket, TicketAIRun, TicketAIQuery, User
from .services import notify_for_event
from .ai_policy import (freeze_scope, upgrade_data_scope, recover_original_source_kinds, complete_data_objects, required_data_keys,
    validate_catalog_attestation, exact_keys, digest, PolicyError, DATA_TABLES, MAX_QUERIES)

SYSTEM_CLIENTS = {'DS4': '110', 'QS4': '120', 'PS4': '100'}
BLOCK_REASONS = {'sap_unavailable', 'authorization_missing', 'context_incomplete', 'evidence_incomplete', 'runtime_error', 'time_limit'}
FAILURE_DETAIL_REASONS = {
    'source_type_missing': 'authorization_missing', 'source_type_not_authorized': 'authorization_missing',
    'plan_outside_scope': 'authorization_missing', 'plan_invalid': 'authorization_missing',
    'cli_version_mismatch': 'authorization_missing', 'unexpected_model_event': 'authorization_missing',
    'executor_error': 'runtime_error',
}
LEASE_DURATION = timedelta(minutes=15)
TOTAL_DURATION = timedelta(minutes=45)
RETRY_DELAYS = (timedelta(minutes=5), timedelta(minutes=10))
MIN_RETRY_DELAY = timedelta(minutes=1)
# Keep five minutes for analysis plus one minute for the dispatcher's next scan.
RETRY_TIME_RESERVE = timedelta(minutes=6)


def enabled():
    return settings.AI_TICKET_AUTOMATION_ENABLED


def acceptance(ticket):
    return ticket.events.filter(kind='status', from_status='pending', to_status='in-progress',
        actor_role='admin', actor__role='admin', actor__is_active=True).order_by('-id').first()


def accepted_scope(ticket, accepted):
    previous = TicketAIRun.objects.filter(ticket=ticket, accepted_event=accepted).exclude(
        accepted_scope={}).order_by('-created_at', '-id').first()
    scope = copy.deepcopy(previous.accepted_scope) if previous else freeze_scope(
        ticket.system, ticket.title, ticket.description, accepted.body, accepted.pk)
    original = freeze_scope(ticket.system, ticket.title, ticket.description, accepted.body, accepted.pk)
    upgrade_data_scope(scope, original)
    if original['restriction_unresolved']:
        scope['restriction_unresolved'] = True
    recover_original_source_kinds(scope, original)
    # Only missing keys on an identified accepted object may be completed.
    # A follow-up cannot replace a bound key, add an object or pair legacy lists.
    if any(required_data_keys(obj, scope) - set(record)
           for obj, records in scope.get('data_objects', {}).items() if obj in DATA_TABLES for record in records):
        followups = ticket.events.filter(kind='reply', actor_id=ticket.owner_id, actor_role='consultant',
            is_ai=False, pk__gt=accepted.pk).order_by('-id')[:100]
        for followup in reversed(list(followups)):
            complete_data_objects(scope, followup.body, followup.pk)
    scope['data_keys'] = {obj: sorted(required_data_keys(obj, scope))
                          for obj in scope['objects'] if obj in DATA_TABLES}
    return scope


def retry_requested(run):
    return Audit.objects.filter(action='ai_ticket_retry_requested', target=str(run.ticket_id),
        actor_id=run.accepted_event.actor_id, detail__run_id=str(run.pk),
        detail__trigger_event=run.trigger_id, detail__accepted_event=run.accepted_event_id).exists()


def trigger_for(ticket):
    if not enabled():
        return None, None, 'disabled'
    if ticket.kind != '问题':
        return None, None, 'unsupported_kind'
    if ticket.status != 'in-progress':
        return None, None, 'not_in_progress'
    if not ticket.owner.is_active:
        return None, None, 'owner_inactive'
    accepted = acceptance(ticket)
    if not accepted:
        return None, None, 'not_accepted'
    answer = ticket.events.filter(kind='reply').filter(Q(is_ai=True) | Q(actor_role__in=['admin', 'ai'])).order_by('-id').first()
    followup = ticket.events.filter(kind='reply', actor_role='consultant', is_ai=False,
        actor_id=ticket.owner_id).order_by('-id').first()
    # A blocked round may be continued only by an explicit operator request.
    # A later human reply or owner follow-up always takes precedence.
    continuation = ticket.events.filter(kind='ai_retry', actor_role='system',
        actor_id=accepted.actor_id, pk__gt=accepted.pk).order_by('-id').first()
    if continuation and continuation.pk > max(answer.pk if answer else 0, followup.pk if followup else 0):
        retry = TicketAIRun.objects.filter(ticket=ticket, trigger=continuation,
            accepted_event=accepted, context_version=ticket.version).first()
        if retry and retry_requested(retry):
            return continuation, accepted, ''
    if answer and (not followup or followup.pk <= answer.pk):
        # Only the trusted operator command can request another turn on an AI answer.
        # A normal AI reply or consultant-authored instruction cannot do this.
        retry = TicketAIRun.objects.filter(ticket=ticket, trigger=answer, accepted_event=accepted).first()
        if answer.is_ai and retry and retry_requested(retry):
            return answer, accepted, ''
        return None, accepted, 'answered'
    trigger = followup if followup and followup.pk > accepted.pk else accepted
    return trigger, accepted, ''


def request_retry(ticket_id, version):
    """Explicit operator action; preserve the old reply/run and all normal lease gates."""
    with transaction.atomic():
        ticket = Ticket.objects.select_for_update().select_related('owner').get(pk=ticket_id)
        trigger, accepted, reason = trigger_for(ticket)
        if reason not in ('', 'answered'):
            return {'outcome': 'skipped', 'reason': reason}
        if ticket.version != version:
            return {'outcome': 'skipped', 'reason': 'version_changed'}
        answer = ticket.events.filter(kind='reply').filter(
            Q(is_ai=True) | Q(actor_role__in=['admin', 'ai'])).order_by('-id').first()
        if not answer or not answer.is_ai:
            return {'outcome': 'skipped', 'reason': 'no_ai_answer'}
        existing = TicketAIRun.objects.filter(ticket=ticket, trigger=answer).first()
        if existing and retry_requested(existing):
            return {'outcome': 'already_requested', 'run_id': str(existing.pk)}
        if reason != 'answered' or existing:
            return {'outcome': 'skipped', 'reason': 'already_actionable'}
        run = TicketAIRun.objects.create(ticket=ticket, trigger=answer, accepted_event=accepted,
            context_version=ticket.version, status='queued', lease_expires_at=timezone.now(), attempts=0)
        Audit.objects.create(actor=accepted.actor, action='ai_ticket_retry_requested', target=str(ticket.pk),
            detail={'run_id': str(run.pk), 'trigger_event': answer.pk, 'accepted_event': accepted.pk,
                'version': ticket.version, 'source': 'explicit_operator_request'})
        return {'outcome': 'queued', 'run_id': str(run.pk), 'version': ticket.version,
            'trigger_event_id': answer.pk}



def request_blocked_followup(ticket_id, version, request_key, proof):
    """Explicit trusted operator action for a single exhausted, accepted round.

    The caller attests it just used the company SAP catalogue with verified
    identity; a plain hash is an integrity check, not an authenticity signature.
    This operation is never exposed to the model or the automated gateway.
    """
    if type(ticket_id) is not int or ticket_id < 1 or type(version) is not int or version < 1:
        raise PolicyError('invalid_request')
    try:
        request_key = uuid.UUID(str(request_key))
    except (TypeError, ValueError, AttributeError):
        raise PolicyError('invalid_request') from None
    exact_keys(proof, {'object', 'kind', 'observed_at', 'evidence'})
    fingerprint = digest({'ticket_id': ticket_id, 'version': version, 'proof': proof})
    with transaction.atomic():
        ticket = Ticket.objects.select_for_update().select_related('owner').get(pk=ticket_id)
        existing = Audit.objects.filter(action='ai_ticket_retry_requested', target=str(ticket.pk),
            detail__request_key=str(request_key)).order_by('-id').first()
        if existing:
            if existing.detail.get('fingerprint') != fingerprint:
                raise PolicyError('request_key_reused')
            retry = TicketAIRun.objects.filter(pk=existing.detail.get('run_id'), ticket=ticket).first()
            if not retry or not retry_requested(retry):
                raise PolicyError('retry_record_invalid')
            return {'outcome': 'already_requested', 'run_id': str(retry.pk)}
        if ticket.version != version:
            return {'outcome': 'skipped', 'reason': 'version_changed'}
        trigger, accepted, reason = trigger_for(ticket)
        if reason:
            return {'outcome': 'skipped', 'reason': reason}
        prior, blocker = run_blocker(ticket, trigger)
        if blocker != 'manual_required' or not prior or prior.status != 'blocked' or prior.accepted_event_id != accepted.pk:
            return {'outcome': 'skipped', 'reason': 'not_manual_required'}
        if prior.context_version != ticket.version:
            return {'outcome': 'skipped', 'reason': 'version_changed'}
        # This recovery path cannot reset a previously consumed SAP query budget.
        if prior.queries.exists():
            return {'outcome': 'skipped', 'reason': 'prior_query_history'}
        observed = parse_datetime(proof['observed_at']) if isinstance(proof['observed_at'], str) else None
        now = timezone.now()
        if not observed or timezone.is_naive(observed) or observed > now + timedelta(seconds=30) or observed < now - timedelta(minutes=5):
            raise PolicyError('catalog_attestation_stale')
        scope = copy.deepcopy(prior.accepted_scope)
        original = freeze_scope(ticket.system, ticket.title, ticket.description, accepted.body, accepted.pk)
        if (scope.get('accepted_event_id') != accepted.pk or scope.get('system') != ticket.system
                or scope.get('client') != SYSTEM_CLIENTS.get(ticket.system)
                or scope.get('source_hash') != original['source_hash']
                or scope.get('restriction_unresolved') or original['restriction_unresolved']
                or proof['object'] not in original['objects']):
            raise PolicyError('accepted_material_mismatch')
        if scope.get('schema') == 'ticket-read-scope/v1':
            upgrade_data_scope(scope, original)
        if scope.get('schema') != 'ticket-read-scope/v2':
            raise PolicyError('scope_unresolved')
        known = original['source_kinds'].get(proof['object'], [])
        if known and proof['kind'] not in known:
            raise PolicyError('source_kind_outside_scope')
        proof_hash = validate_catalog_attestation(scope, proof['object'], proof['kind'], proof['evidence'])
        scope['source_kinds'][proof['object']] = [proof['kind']]
        scope.setdefault('source_kind_attestations', {})[proof['object']] = {
            'kind': proof['kind'], 'system': scope['system'], 'client': scope['client'],
            'observed_at': observed.isoformat(), 'sha256': proof_hash,
            'source': 'trusted_operator_catalog_attestation',
        }
        ticket.version += 1
        ticket.updated_at = now
        ticket.save(update_fields=['version', 'updated_at'])
        item = Event.objects.create(ticket=ticket, actor=accepted.actor, actor_name='系统', actor_role='system',
            kind='ai_retry', body='已按已受理范围请求 AI 继续分析', version=ticket.version)
        retry = TicketAIRun.objects.create(ticket=ticket, trigger=item, accepted_event=accepted,
            context_version=ticket.version, status='queued', accepted_scope=scope, lease_expires_at=now)
        Audit.objects.create(actor=accepted.actor, action='ai_ticket_retry_requested', target=str(ticket.pk), detail={
            'run_id': str(retry.pk), 'trigger_event': item.pk, 'accepted_event': accepted.pk,
            'version': ticket.version, 'source': 'explicit_blocked_operator_request',
            'prior_run_id': str(prior.pk), 'request_key': str(request_key), 'fingerprint': fingerprint,
            'object': proof['object'], 'kind': proof['kind'], 'catalog_sha256': proof_hash,
            'catalog_observed_at': observed.isoformat(),
        })
        return {'outcome': 'queued', 'run_id': str(retry.pk), 'version': ticket.version, 'trigger_event_id': item.pk}


def run_blocker(ticket, trigger):
    run = TicketAIRun.objects.filter(ticket=ticket, trigger=trigger).first()
    if not run:
        return None, ''
    if run.status == 'replied':
        return run, 'answered'
    if run.status == 'superseded':
        return run, 'answered'
    if run.reason == 'time_limit' or (run.deadline_at and run.deadline_at <= timezone.now()):
        return run, 'manual_required'
    if run.status == 'running' and run.lease_expires_at > timezone.now():
        return run, 'running'
    if run.context_version == ticket.version and run.attempts >= 3:
        return run, 'manual_required'
    if run.context_version == ticket.version and run.status == 'blocked':
        if run.attempts >= 3:
            return run, 'manual_required'
        if run.retry_after is None:
            detail = Audit.objects.filter(action='ai_ticket_blocked', target=str(ticket.pk),
                detail__run_id=str(run.pk)).order_by('-id').values_list('detail__detail_code', flat=True).first()
            if detail == 'source_type_missing':
                return run, 'manual_required'
        if run.retry_after and run.retry_after > timezone.now():
            return run, 'retry_wait'
    return run, ''


def enqueue_after_event(ticket, item):
    """Queue in the same transaction as the accepted status or new message."""
    if not enabled():
        return None
    if item.kind == 'status' and item.to_status != 'in-progress':
        TicketAIRun.objects.filter(ticket=ticket, status__in=['queued', 'running']).update(
            status='superseded', reason='not_in_progress', updated_at=timezone.now())
        return None
    if not ((item.kind == 'status' and item.from_status == 'pending' and item.to_status == 'in-progress')
            or item.kind == 'reply'):
        return None
    trigger, accepted, reason = trigger_for(ticket)
    if reason:
        # A human answer or status change invalidates any in-flight answer.
        if item.kind == 'reply' and item.actor_role == 'admin':
            TicketAIRun.objects.filter(ticket=ticket, status__in=['queued', 'running']).update(
                status='superseded', reason='answered', updated_at=timezone.now())
        return None
    TicketAIRun.objects.filter(ticket=ticket, status__in=['queued', 'running']).exclude(trigger=trigger).update(
        status='superseded', reason='version_changed', updated_at=timezone.now())
    run, created = TicketAIRun.objects.get_or_create(ticket=ticket, trigger=trigger, defaults={
        'accepted_event': accepted, 'context_version': ticket.version, 'status': 'queued',
        'accepted_scope': accepted_scope(ticket, accepted),
        'lease_expires_at': timezone.now(), 'attempts': 0,
    })
    if created:
        Audit.objects.create(actor=accepted.actor, action='ai_ticket_enqueued', target=str(ticket.pk),
            detail={'run_id': str(run.pk), 'trigger_event': trigger.pk, 'version': ticket.version})
    return run


def refresh_after_schedule(ticket):
    """Rescan an existing run after metadata changes without authorizing a new AI turn.

    Called with the ticket locked. Keep retry counts, deadline, authorization and
    blocked gates; old workers lose their lease and cannot publish stale context.
    """
    TicketAIRun.objects.filter(ticket=ticket, status__in=['queued', 'running']).update(
        context_version=ticket.version, status='queued', lease_token=uuid.uuid4(),
        lease_expires_at=timezone.now(), updated_at=timezone.now())
    TicketAIRun.objects.filter(ticket=ticket, status='blocked').update(context_version=ticket.version)


def candidate_page(after=0, limit=10):
    if after < 0 or not 1 <= limit <= 50:
        raise ValidationError('无效的分页范围。')
    if not enabled():
        return {'enabled': False, 'results': [], 'has_more': False, 'next_after_id': after}
    now=timezone.now()
    TicketAIRun.objects.filter(status__in=['running','blocked'], deadline_at__lte=now).exclude(
        reason='time_limit').update(status='blocked', reason='time_limit', retry_after=None, updated_at=now)
    accepted = Event.objects.filter(ticket_id=OuterRef('pk'), kind='status', from_status='pending',
        to_status='in-progress', actor_role='admin', actor__role='admin', actor__is_active=True)
    # Scan bounded pages even when every ticket is skipped, so the caller can advance.
    scanned = list(Ticket.objects.filter(pk__gt=after, kind='问题', status='in-progress', owner__is_active=True)
        .annotate(accepted=Exists(accepted)).filter(accepted=True).select_related('owner').order_by('id')[:limit+1])
    results = []
    for ticket in scanned[:limit]:
        trigger, accepted_event, reason = trigger_for(ticket)
        if reason or run_blocker(ticket, trigger)[1]:
            continue
        results.append({'id': ticket.pk, 'number': ticket.number, 'title': ticket.title,
            'preview': ticket.description[:1200], 'system': ticket.system, 'kind': ticket.kind, 'module': ticket.module,
            'version': ticket.version, 'trigger_event_id': trigger.pk, 'accepted_event_id': accepted_event.pk})
    return {'enabled': True, 'untrusted_evidence': True, 'results': results, 'has_more': len(scanned)>limit,
        'next_after_id': scanned[min(len(scanned),limit)-1].pk if scanned else after}


def ticket_context(ticket):
    history = list(ticket.events.prefetch_related('attachments').order_by('-id')[:31])
    incomplete = len(history)>30
    history = history[:30]
    remaining, events = 30000, []
    for item in history:
        body = item.body[:remaining]
        incomplete |= len(body)!=len(item.body)
        remaining -= len(body)
        events.append({'id':item.pk, 'kind':item.kind, 'actor_role':item.actor_role, 'is_ai':item.is_ai,
            'body':body, 'created_at':item.created_at.isoformat(),
            'attachments':[{'id':a.pk,'name':a.original_name} for a in item.attachments.all()]})
    return {'untrusted_evidence':True, 'id':ticket.pk, 'number':ticket.number, 'title':ticket.title,
        'description':ticket.description, 'system':ticket.system, 'module':ticket.module, 'kind':ticket.kind, 'status':ticket.status,
        'version':ticket.version, 'events':list(reversed(events)), 'incomplete_context':incomplete}


def claim(ticket_id, version):
    with transaction.atomic():
        ticket=Ticket.objects.select_for_update().select_related('owner').get(pk=ticket_id)
        trigger, accepted, reason=trigger_for(ticket)
        if reason: return {'outcome':'skipped','reason':reason}
        if ticket.version!=version: return {'outcome':'skipped','reason':'version_changed'}
        run, reason=run_blocker(ticket,trigger)
        if reason: return {'outcome':'skipped','reason':reason}
        previous_reason=run.reason if run else ''
        now=timezone.now()
        deadline=run.deadline_at if run and run.deadline_at else now+TOTAL_DURATION
        values={'accepted_event':accepted,'context_version':ticket.version,'status':'running',
            'lease_token':uuid.uuid4(),'lease_expires_at':min(now+LEASE_DURATION, deadline),
            'deadline_at':deadline,
            'retry_after':None,'reason':'','attempts':1,'updated_at':timezone.now()}
        values['accepted_scope'] = accepted_scope(ticket, accepted)
        if run:
            values['attempts']=run.attempts+1 if run.context_version==ticket.version else 1
            for key,value in values.items(): setattr(run,key,value)
            run.save()
        else:
            run=TicketAIRun.objects.create(ticket=ticket,trigger=trigger,**values)
        Audit.objects.create(actor=accepted.actor,action='ai_ticket_claimed',target=str(ticket.pk),
            detail={'run_id':str(run.pk),'trigger_event':trigger.pk,'version':ticket.version})
        return {'outcome':'claimed','run_id':str(run.pk),'lease_token':str(run.lease_token),
            'lease_expires_at':run.lease_expires_at.isoformat(),'deadline_at':run.deadline_at.isoformat(),
            'previous_reason':previous_reason,
            'manual_retry':retry_requested(run),
            'accepted_by':accepted.actor_id,'accepted_at':accepted.created_at.isoformat(),
            'acceptance_note':accepted.body,'context':ticket_context(ticket),
            'read_scope':run.accepted_scope,'query_limit':MAX_QUERIES,'queries_used':run.queries.count()}


def renew(run_id, lease_token):
    """Extend only the current worker's lease, never beyond the run's total deadline."""
    with transaction.atomic():
        parent=TicketAIRun.objects.get(pk=run_id)
        ticket=Ticket.objects.select_for_update().get(pk=parent.ticket_id)
        run=TicketAIRun.objects.select_for_update().get(pk=run_id)
        now=timezone.now()
        if str(run.lease_token)!=str(lease_token):
            return {'outcome':'skipped','reason':'lease_changed'}
        if run.status!='running':
            return {'outcome':'skipped','reason':run.reason or 'run_changed'}
        deadline=run.deadline_at or run.updated_at+TOTAL_DURATION
        if deadline<=now:
            run.deadline_at=deadline;run.status='blocked';run.reason='time_limit'
            run.updated_at=now;run.save(update_fields=['deadline_at','status','reason','updated_at'])
            return {'outcome':'blocked','reason':'time_limit','manual_required':True}
        if run.lease_expires_at<=now:
            return {'outcome':'skipped','reason':'lease_expired'}
        trigger,accepted,reason=trigger_for(ticket)
        if not reason and (ticket.version!=run.context_version or trigger.pk!=run.trigger_id):
            reason='version_changed'
        if reason:
            run.status='superseded';run.reason=reason;run.updated_at=now
            run.save(update_fields=['status','reason','updated_at'])
            return {'outcome':'skipped','reason':reason}
        run.deadline_at=deadline
        run.lease_expires_at=min(now+LEASE_DURATION,deadline)
        run.updated_at=now
        run.save(update_fields=['deadline_at','lease_expires_at','updated_at'])
        return {'outcome':'renewed','lease_expires_at':run.lease_expires_at.isoformat(),
            'deadline_at':deadline.isoformat()}


def validate_payload(payload):
    if not isinstance(payload,dict) or set(payload)-{'category','body','evidence'}:
        raise ValidationError('无效的 AI 回复字段。')
    category=payload.get('category')
    if category not in ['query','clarification']:
        raise ValidationError('仅允许只读分析或补充信息请求。')
    body=payload.get('body')
    if not isinstance(body,str) or not body.strip(): raise ValidationError('回复正文不能为空。')
    body=body.strip()
    if not body.startswith('AI：'): body='AI：'+body
    if category=='clarification' and not body.startswith('AI：需要补充信息'):
        body='AI：需要补充信息\n\n'+body.removeprefix('AI：')
    if len(body)>10000: raise ValidationError('回复超过长度限制。')
    evidence=payload.get('evidence')
    if not isinstance(evidence,list) or len(evidence)>20 or (category=='query' and not evidence):
        raise ValidationError('分析结论必须附带实际 SAP 查询证据引用。')
    references=[]
    for entry in evidence:
        if not isinstance(entry,dict) or set(entry)!={'system','client','reference'}:
            raise ValidationError('证据字段无效。')
        if entry['system'] not in SYSTEM_CLIENTS or SYSTEM_CLIENTS[entry['system']]!=entry['client']:
            raise ValidationError('证据系统和客户端不匹配。')
        reference=entry['reference']
        if not isinstance(reference,str) or not reference.strip() or len(reference)>500:
            raise ValidationError('证据引用无效。')
        references.append({**entry,'reference':reference.strip()})
    return category,body,references


def publish(run_id, lease_token, payload):
    category,body,evidence=validate_payload(payload)
    with transaction.atomic():
        # Ticket is always locked before run, matching claim and ordinary replies.
        parent=TicketAIRun.objects.get(pk=run_id)
        ticket=Ticket.objects.select_for_update().select_related('owner').get(pk=parent.ticket_id)
        run=TicketAIRun.objects.select_for_update().get(pk=run_id)
        if str(run.lease_token)!=str(lease_token): return {'outcome':'skipped','reason':'lease_changed'}
        if run.status=='replied': return {'outcome':'already_replied','event_id':run.reply_event_id}
        if run.status=='superseded': return {'outcome':'skipped','reason':run.reason or 'run_changed'}
        if run.deadline_at and run.deadline_at<=timezone.now():
            run.status='blocked';run.reason='time_limit';run.updated_at=timezone.now();run.save()
            return {'outcome':'skipped','reason':'time_limit'}
        if run.status!='running' or run.lease_expires_at<=timezone.now(): return {'outcome':'skipped','reason':'lease_expired'}
        trigger,accepted,reason=trigger_for(ticket)
        if not reason and (ticket.version!=run.context_version or trigger.pk!=run.trigger_id): reason='version_changed'
        if reason:
            run.status='superseded';run.reason=reason;run.updated_at=timezone.now();run.save()
            return {'outcome':'skipped','reason':reason}
        # References are minted by the trusted executor, never accepted as free text
        # from a model. Bind every receipt to this exact run, version and lease.
        for reference in evidence:
            try:
                prefix, query_id = reference['reference'].split(':', 1)
                if prefix != 'sap-ticket-query': raise ValueError()
                receipt = TicketAIQuery.objects.get(pk=uuid.UUID(query_id), run=run,
                    status='succeeded', context_version=run.context_version, lease_token=run.lease_token)
            except (ValueError, TicketAIQuery.DoesNotExist):
                raise ValidationError('SAP 证据必须来自本轮受控查询回执。')
            if not receipt.result_hash or reference['system'] != ticket.system or reference['system'] != run.accepted_scope.get('system') or reference['client'] != SYSTEM_CLIENTS.get(ticket.system):
                raise ValidationError('SAP 证据与本轮受理范围不匹配。')
        ticket.version+=1;ticket.updated_at=timezone.now();ticket.save(update_fields=['version','updated_at'])
        item=Event.objects.create(ticket=ticket,actor=accepted.actor,actor_name='AI · SAP 助手',actor_role='ai',
            kind='reply',body=body,is_ai=True,version=ticket.version)
        notify_for_event(item)
        run.status='replied';run.reply_event=item;run.reason='';run.updated_at=timezone.now();run.save()
        Audit.objects.create(actor=accepted.actor,action='ai_ticket_reply',target=str(ticket.pk),detail={
            'run_id':str(run.pk),'event_id':item.pk,'trigger_event':run.trigger_id,'context_version':run.context_version,
            'category':category,'evidence':evidence})
        return {'outcome':'replied','ticket_id':ticket.pk,'event_id':item.pk,'version':ticket.version}


def defer(run_id,lease_token,reason,detail_code=''):
    if reason not in BLOCK_REASONS or not isinstance(detail_code, str) or (detail_code and FAILURE_DETAIL_REASONS.get(detail_code) != reason):
        raise ValidationError('无效的受阻原因。')
    with transaction.atomic():
        run=TicketAIRun.objects.select_for_update().get(pk=run_id)
        now = timezone.now()
        if str(run.lease_token)!=str(lease_token) or run.status!='running' or run.lease_expires_at<=now:
            return {'outcome':'skipped','reason':'run_changed'}
        run.status='blocked';run.reason=reason;run.updated_at=now
        run.retry_after = None
        manual_required = reason == 'time_limit' or run.attempts >= 3 or detail_code == 'source_type_missing'
        if not manual_required:
            delay = RETRY_DELAYS[0 if run.attempts == 1 else 1]
            if run.deadline_at:
                delay = min(delay, run.deadline_at - now - RETRY_TIME_RESERVE)
            if delay < MIN_RETRY_DELAY:
                run.reason = 'time_limit'
                manual_required = True
            else:
                run.retry_after = now + delay
        run.save()
        detail = {'run_id': str(run.pk), 'reason': reason, 'manual_required': manual_required}
        if run.reason != reason:
            detail['terminal_reason'] = run.reason
        if detail_code:
            detail['detail_code'] = detail_code
        Audit.objects.create(actor=run.accepted_event.actor, action='ai_ticket_blocked', target=str(run.ticket_id), detail=detail)
        return {'outcome':'blocked','reason':run.reason,'manual_required':manual_required}


FAILURE_LABELS = {
    'authorization_missing': '身份或范围校验受阻', 'sap_unavailable': 'SAP 暂不可用',
    'context_incomplete': '上下文信息不足', 'evidence_incomplete': '查询证据不完整',
    'runtime_error': '执行程序异常', 'time_limit': '本轮处理时间已用尽',
}
FAILURE_DETAIL_LABELS = {
    'source_type_missing': '需要确认对象源码类型',
    'source_type_not_authorized': '源码类型不在受理范围',
    'plan_outside_scope': '查询计划超出受理范围', 'plan_invalid': '查询计划未通过安全校验',
    'cli_version_mismatch': 'AI 程序版本不匹配', 'unexpected_model_event': '检测到不允许的模型执行事件',
    'executor_error': '执行程序异常',
}


def last_failure_label(run):
    # Expiry can replace run.reason; preserve the earlier, bounded audit category.
    detail = Audit.objects.filter(action='ai_ticket_blocked', target=str(run.ticket_id),
        detail__run_id=str(run.pk)).order_by('-id').values_list('detail', flat=True).first()
    if not isinstance(detail, dict):
        return FAILURE_LABELS.get(run.reason, '')
    return FAILURE_DETAIL_LABELS.get(detail.get('detail_code'), '') or FAILURE_LABELS.get(detail.get('reason'), '')


def ticket_ai_state(ticket):
    trigger,accepted,reason=trigger_for(ticket)
    labels={'disabled':'AI 跟进尚未启用','not_in_progress':'AI 仅跟进已受理且处理中的工单',
        'unsupported_kind':'AI 仅跟进需求类型为“问题”的工单',
        'not_accepted':'等待管理员受理','owner_inactive':'提交人账号已停用',
        'answered':'本轮已回复；顾问补充后继续分析','running':'AI 正在分析本轮问题',
        'retry_wait':'AI 查询暂时受阻，稍后重试','manual_required':'AI 分析受阻，需要管理员处理'}
    run=None
    if not reason: run,reason=run_blocker(ticket,trigger)
    message=labels.get(reason,'已排队；本机在线时通常约 1 分钟内开始分析')
    if reason=='manual_required' and run and (run.reason=='time_limit' or
            (run.deadline_at and run.deadline_at<=timezone.now())):
        message = ('AI 本轮剩余时间不足以再次重试，需管理员处理'
            if run.deadline_at and run.deadline_at > timezone.now()
            else 'AI 分析已达到 45 分钟上限，需管理员处理')
    if reason in {'manual_required', 'retry_wait'} and run:
        failure = last_failure_label(run)
        if failure and failure != '本轮处理时间已用尽':
            message = message.rstrip('。') + '；最近受阻：' + failure + '。'
    return {'enabled':enabled(),'state':reason or 'queued','message':message}
