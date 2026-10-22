"""One development queue: explicit, reviewed schedule changes; no automatic shifts."""
import hashlib
import json
from collections import Counter
from zoneinfo import ZoneInfo

from django.core import signing
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from .errors import Conflict
from .models import Audit, Delivery, Event, ExpediteRequest, Notification, ScheduleBatch, Ticket, User
from .services import apply_estimate, estimate_description, event, notify_for_event

EDITABLE = ['pending', 'in-progress']
PREVIEW_SALT = 'desk.schedule-batch.v1'


def today():
    return timezone.localdate(timezone=ZoneInfo('Asia/Shanghai'))


def require_admin(user):
    if user.role != 'admin':
        raise PermissionDenied('只有管理员可以调整开发排期。')


def finish_request(request, user, status, note):
    request.status = status
    request.handled_by = user
    request.handled_at = timezone.now()
    request.decision_note = note
    request.version += 1
    request.save(update_fields=['status', 'handled_by', 'handled_at', 'decision_note', 'version'])


def reconcile_expedite(ticket, user, note):
    """Called with the ticket locked; preserve the ticket's existing notification."""
    request = ExpediteRequest.objects.select_for_update().filter(ticket=ticket, status='pending').first()
    if not request:
        return
    if ticket.status not in EDITABLE:
        finish_request(request, user, 'cancelled', '工单已进入验收或完成阶段，加急申请已结束。')
    elif ticket.estimated_completion_date and ticket.estimated_completion_date <= request.requested_date:
        finish_request(request, user, 'approved', note)


def create_expedite(user, ticket_id, data):
    if user.role != 'consultant':
        raise PermissionDenied('请使用工单提交人账号申请加急。')
    with transaction.atomic():
        ticket = Ticket.objects.select_for_update().filter(owner=user).get(pk=ticket_id)
        existing = ExpediteRequest.objects.filter(requester=user, request_key=data['request_key']).first()
        reason = data['reason'].strip()
        if existing:
            if (existing.ticket_id, existing.requested_date, existing.reason) != (ticket.pk, data['requested_date'], reason):
                raise Conflict('此提交标识已用于其他加急申请，请刷新后重试。')
            return existing, False
        if ticket.version != data['version']:
            raise Conflict()
        if ticket.status not in EDITABLE:
            raise ValidationError('只有待受理或处理中的工单可以申请加急。')
        target = data['requested_date']
        if not reason:
            raise ValidationError('请填写加急原因及业务影响。')
        if target < today() or target.year >= 9999:
            raise ValidationError('希望完成日期不能早于今天或超出支持范围。')
        if ticket.estimated_completion_date and target >= ticket.estimated_completion_date:
            raise ValidationError('希望完成日期应早于当前预计完成日期；其他情况可通过继续沟通说明。')
        if ExpediteRequest.objects.filter(ticket=ticket, status='pending').exists():
            raise ValidationError('此工单已有待评估的加急申请，请等待管理员处理。')
        request = ExpediteRequest.objects.create(ticket=ticket, requester=user,
            request_key=data['request_key'], requested_date=target, reason=reason)
        ticket.version += 1
        ticket.updated_at = timezone.now()
        ticket.save(update_fields=['version', 'updated_at'])
        item = event(ticket, user, 'expedite', body=f'申请加急：希望于 {target} 前完成开发。原因及业务影响：{reason}')
        from .ai import refresh_after_schedule
        refresh_after_schedule(ticket)
        notify_for_event(item)
        Audit.objects.create(actor=user, action='expedite_requested', target=str(ticket.pk), detail={'request_id': request.pk})
        return request, True


def reject_expedite(user, request_id, data):
    require_admin(user)
    with transaction.atomic():
        parent = ExpediteRequest.objects.get(pk=request_id)
        ticket = Ticket.objects.select_for_update().get(pk=parent.ticket_id)
        request = ExpediteRequest.objects.select_for_update().get(pk=request_id)
        if ticket.version != data['ticket_version'] or request.version != data['version']:
            raise Conflict()
        if request.status != 'pending' or ticket.status not in EDITABLE:
            raise Conflict('该加急申请已处理或工单状态已变化，请刷新。')
        reason = data['reason'].strip()
        if not reason:
            raise ValidationError('请填写未能安排加急的原因。')
        finish_request(request, user, 'rejected', reason)
        ticket.version += 1
        ticket.updated_at = timezone.now()
        ticket.save(update_fields=['version', 'updated_at'])
        item = event(ticket, user, 'expedite', body=f'加急申请未通过：{reason}；原开发排期保持不变。')
        from .ai import refresh_after_schedule
        refresh_after_schedule(ticket)
        notify_for_event(item)
        Audit.objects.create(actor=user, action='expedite_rejected', target=str(ticket.pk), detail={'request_id': request.pk})
        return request


def fingerprint(data):
    payload = {'request_key': str(data['request_key']), 'reason': data['reason'].strip(),
        'expedite_request_id': data.get('expedite_request_id'),
        'expedite_request_version': data.get('expedite_request_version'),
        'changes': [{'ticket_id': row['ticket_id'], 'version': row['version'],
            'estimated_completion_date': row['estimated_completion_date'].isoformat() if row['estimated_completion_date'] else None}
            for row in sorted(data['changes'], key=lambda row: row['ticket_id'])]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def prepare_batch(user, data, *, lock=False):
    require_admin(user)
    ids = [row['ticket_id'] for row in data['changes']]
    if len(ids) != len(set(ids)):
        raise ValidationError('同一批次不能重复选择工单。')
    qs = Ticket.objects.order_by('id')
    if lock:
        qs = qs.select_for_update()
    tickets = {t.pk: t for t in qs.filter(pk__in=ids)}
    # Fetch owners separately: PostgreSQL must only lock tickets, not nullable joins.
    owners = User.objects.in_bulk({t.owner_id for t in tickets.values()})
    if len(tickets) != len(ids):
        raise ValidationError('部分工单不存在，请刷新开发队列。')
    changes = []
    for row in data['changes']:
        t = tickets[row['ticket_id']]
        if t.version != row['version']:
            raise Conflict(f'{t.number} 已被更新，请刷新队列后重新评估。')
        if t.status not in EDITABLE:
            raise ValidationError(f'{t.number} 已进入验收或完成阶段，不能参与批量排期。')
        value, previous = row['estimated_completion_date'], t.estimated_completion_date
        if value == previous:
            continue
        apply_estimate(t, value, data['reason'].strip())
        kind = 'scheduled' if previous is None else 'cleared' if value is None else 'advanced' if value < previous else 'delayed'
        changes.append({'ticket_id': t.pk, 'number': t.number, 'title': t.title,
            'owner_id': t.owner_id, 'owner_name': owners[t.owner_id].name,
            'version': t.version, 'previous_date': str(previous) if previous else None,
            'new_date': str(value) if value else None, 'kind': kind})
    if not changes:
        raise ValidationError('没有实际发生日期变化的工单，无需保存。')
    request = None
    if data.get('expedite_request_id'):
        query = ExpediteRequest.objects
        if lock:
            query = query.select_for_update()
        request = query.filter(pk=data['expedite_request_id']).first()
        if not request or request.status != 'pending' or request.version != data.get('expedite_request_version'):
            raise Conflict('加急申请已处理或已变化，请刷新后重新评估。')
        changed = next((row for row in changes if row['ticket_id'] == request.ticket_id), None)
        if not changed or not changed['new_date'] or tickets[request.ticket_id].estimated_completion_date > request.requested_date:
            raise ValidationError('处理加急申请时，该工单的新日期须不晚于申请日期；无法满足时请说明原因并拒绝申请。')
    recipients = {row['owner_id'] for row in changes if owners[row['owner_id']].is_active and row['owner_id'] != user.pk}
    counts = Counter(row['kind'] for row in changes)
    summary = {'changes': changes, 'changed_count': len(changes), 'advanced_count': counts['advanced'],
        'delayed_count': counts['delayed'], 'scheduled_count': counts['scheduled'], 'cleared_count': counts['cleared'],
        'recipient_count': len(recipients), 'reason': data['reason'].strip(), 'expedite_request_id': request.pk if request else None}
    return tickets, request, summary


def preview_batch(user, data):
    _, _, summary = prepare_batch(user, data)
    token = signing.dumps({'actor': user.pk, 'fingerprint': fingerprint(data)}, salt=PREVIEW_SALT)
    return {**summary, 'preview_token': token}


def notify_batch(items, batch):
    grouped = {}
    notices = []
    active_owners = set(User.objects.filter(pk__in=[item.ticket.owner_id for item in items], is_active=True).values_list('pk', flat=True))
    for item in items:
        owner_id = item.ticket.owner_id
        if owner_id == batch.actor_id or owner_id not in active_owners:
            continue
        notices.append(Notification(recipient_id=owner_id, event=item))
        grouped.setdefault(owner_id, item)
    Notification.objects.bulk_create(notices)
    Delivery.objects.bulk_create([Delivery(event=item, channel='group_consultant', recipient_id=owner_id, schedule_batch=batch)
                                 for owner_id, item in grouped.items()])


def commit_batch(user, data):
    require_admin(user)
    digest = fingerprint(data)
    with transaction.atomic():
        # Serialize the actor's idempotency key; all ticket locks follow ID order.
        User.objects.select_for_update().get(pk=user.pk)
        existing = ScheduleBatch.objects.filter(actor=user, request_key=data['request_key']).first()
        if existing:
            if existing.fingerprint != digest:
                raise Conflict('此批次已用于另一份排期，请重新预览。')
            return existing.result
        try:
            proof = signing.loads(data.get('preview_token', ''), salt=PREVIEW_SALT, max_age=900)
        except signing.BadSignature:
            raise ValidationError('排期预览已失效，请重新预览后确认。') from None
        if proof != {'actor': user.pk, 'fingerprint': digest}:
            raise ValidationError('排期内容已变化，请重新预览。')
        tickets, request, summary = prepare_batch(user, data, lock=True)
        batch = ScheduleBatch.objects.create(actor=user, request_key=data['request_key'], fingerprint=digest,
            reason=summary['reason'], source_request=request)
        items = []
        from .ai import refresh_after_schedule
        for row in summary['changes']:
            t = tickets[row['ticket_id']]
            t.version += 1
            t.updated_at = timezone.now()
            t.save(update_fields=['estimated_completion_date', 'estimate_needs_review', 'version', 'updated_at'])
            item = event(t, user, 'schedule', schedule_batch=batch,
                previous_estimated_completion_date=row['previous_date'], estimated_completion_date=row['new_date'],
                body=estimate_description(row['previous_date'], row['new_date']) + '；原因：' + batch.reason)
            items.append(item)
            reconcile_expedite(t, user, batch.reason)
            refresh_after_schedule(t)
        notify_batch(items, batch)
        batch.result = {**summary, 'batch_id': str(batch.pk)}
        batch.save(update_fields=['result'])
        Audit.objects.create(actor=user, action='schedule_batch_created', target=str(batch.pk),
            detail={'changed_count': summary['changed_count'], 'recipient_count': summary['recipient_count']})
        return batch.result
