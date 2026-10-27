"""Bounded operational aggregates; no ticket text, credentials or SAP calls."""
import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Exists, F, Max, Min, OuterRef, Q
from django.utils import timezone

from .models import Audit, Event, Ticket, TicketAIExecutorState, TicketAIRun


CAPABILITIES = ('model_cli', 'sap_tools', 'production_identity', 'proxy')
FAILURE_REASONS = {'sap_unavailable', 'authorization_missing', 'context_incomplete',
                   'evidence_incomplete', 'runtime_error', 'time_limit'}
logger = logging.getLogger(__name__)


def record_executor_heartbeat(capabilities):
    if not isinstance(capabilities, dict) or set(capabilities) != set(CAPABILITIES) or any(
            type(capabilities[name]) is not bool for name in CAPABILITIES):
        raise ValueError('invalid_executor_health')
    now = timezone.now()
    TicketAIExecutorState.objects.update_or_create(pk='primary', defaults={
        'last_seen_at': now, 'last_scan_at': now, 'capabilities': capabilities,
    })


def touch_worker_heartbeat():
    # Monitoring failure must never invalidate a successful lease renewal.
    try:
        TicketAIExecutorState.objects.update_or_create(pk='primary', defaults={'last_seen_at': timezone.now()})
    except Exception:
        logger.warning('AI executor heartbeat update failed')


def age_seconds(value, now):
    return max(0, int((now - value).total_seconds())) if value else None


def monitor_summary(now=None):
    now = now or timezone.now()
    enabled = settings.AI_TICKET_AUTOMATION_ENABLED
    node = TicketAIExecutorState.objects.filter(pk='primary').first()
    seen = node.last_seen_at if node else None
    scan = node.last_scan_at if node else None
    age = age_seconds(seen, now)
    grace_age = age_seconds(node.created_at, now) if node else None
    stale = age is not None and age > settings.AI_HEARTBEAT_STALE_SECONDS
    absent = age is None and (grace_age is None or grace_age > settings.AI_HEARTBEAT_STALE_SECONDS)
    offline = bool(enabled and (stale or absent))
    status = 'disabled' if not enabled else 'offline' if offline else 'online' if seen else 'awaiting'
    capabilities = {key: node.capabilities.get(key) if node and type(node.capabilities.get(key)) is bool else None
                    for key in CAPABILITIES}

    eligible = TicketAIRun.objects.filter(ticket__status='in-progress', ticket__kind='问题',
        ticket__owner__is_active=True, accepted_event__actor_role='admin',
        accepted_event__actor__role='admin', accepted_event__actor__is_active=True)
    pending = eligible.filter(context_version=F('ticket__version'))
    terminal = Q(reason='time_limit') | Q(deadline_at__lte=now) | Q(attempts__gte=3)
    due = pending.filter(Q(status='queued') | Q(status='blocked', retry_after__lte=now) |
                         Q(status='running', lease_expires_at__lte=now)).exclude(terminal)
    oldest = due.aggregate(value=Min('trigger__created_at'))['value']
    newer_reply = Event.objects.filter(ticket_id=OuterRef('ticket_id'), kind='reply',
                                       pk__gt=OuterRef('reply_event_id'))
    waiting = eligible.filter(status='replied', reply_event__body__startswith='AI：需要补充信息').annotate(
        newer_reply=Exists(newer_reply)).filter(newer_reply=False).count()
    blocked = pending.filter(status='blocked')
    manual = (blocked.filter(Q(retry_after__isnull=True) | terminal).count()
              + pending.filter(status='queued').filter(terminal).count()
              + pending.filter(status='running').filter(Q(deadline_at__lte=now) |
                    Q(lease_expires_at__lte=now, attempts__gte=3)).count())
    accepted = Event.objects.filter(ticket_id=OuterRef('pk'), kind='status', from_status='pending',
        to_status='in-progress', actor_role='admin', actor__role='admin', actor__is_active=True)
    problem_tickets = Ticket.objects.filter(status='in-progress', kind='问题')
    needs_acceptance = problem_tickets.filter(owner__is_active=True).annotate(
        valid_acceptance=Exists(accepted)).filter(valid_acceptance=False).count()
    inactive_submitters = problem_tickets.filter(owner__is_active=False).count()
    recent = Audit.objects.filter(action__in=['ai_ticket_blocked', 'ai_ticket_reply'],
        created_at__gte=now-timedelta(days=1)).order_by('-id').values('action', 'created_at', 'detail__reason')[:100]
    failure_count, reasons, last_failure = 0, {}, None
    for item in recent:
        if item['action'] == 'ai_ticket_reply':
            break
        failure_count += 1
        reason = item['detail__reason'] if item['detail__reason'] in FAILURE_REASONS else 'other'
        reasons[reason] = reasons.get(reason, 0) + 1
        if last_failure is None:
            last_failure = item['created_at']
    last_reply = TicketAIRun.objects.filter(status='replied', reply_event__isnull=False).aggregate(
        value=Max('reply_event__created_at'))['value']
    oldest_seconds = age_seconds(oldest, now)
    return {
        'enabled': enabled,
        'node': {'status': status, 'last_seen_at': seen, 'last_scan_at': scan, 'capabilities': capabilities},
        'queue': {'queued': due.count(),
                  'running': pending.filter(status='running', lease_expires_at__gt=now).exclude(deadline_at__lte=now).count(),
                  'retry_wait': blocked.filter(retry_after__gt=now).exclude(terminal).count(),
                  'manual_required': manual,
                  'waiting_for_information': waiting, 'needs_acceptance': needs_acceptance,
                  'inactive_submitters': inactive_submitters, 'oldest_wait_seconds': oldest_seconds},
        'failures': {'consecutive': failure_count, 'by_reason': reasons, 'last_failure_at': last_failure},
        'last_reply_at': last_reply,
        'alerts': {'offline': offline, 'readiness': bool(enabled and any(value is False for value in capabilities.values())),
                   'backlog': bool(enabled and oldest_seconds is not None and oldest_seconds >= settings.AI_BACKLOG_ALERT_SECONDS),
                   'failure_streak': bool(enabled and failure_count >= settings.AI_FAILURE_ALERT_COUNT),
                   'manual': bool(enabled and (manual or needs_acceptance or inactive_submitters))},
        'thresholds': {'heartbeat_seconds': settings.AI_HEARTBEAT_STALE_SECONDS,
                       'backlog_seconds': settings.AI_BACKLOG_ALERT_SECONDS,
                       'failure_count': settings.AI_FAILURE_ALERT_COUNT},
    }
