import hashlib
import json
from django.db import transaction
from zoneinfo import ZoneInfo
from django.db.models import Exists, OuterRef, Q, F
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from .errors import Conflict
from .models import User, Ticket, Event, Attachment, Notification, Delivery, SYSTEMS
from .uploads import validate_uploads
from .identifiers import positive_id

def scoped_tickets(user):
    qs = Ticket.objects.all()
    if user.role != 'admin':
        qs = qs.filter(owner=user)
    unread = Notification.objects.filter(recipient=user, event__ticket_id=OuterRef('pk'), read_at__isnull=True)
    return qs.select_related('owner').annotate(unread=Exists(unread))

def filtered_tickets(user, params):
    qs = scoped_tickets(user)
    for key in ['status', 'module', 'priority']:
        if params.get(key):
            qs = qs.filter(**{key: params[key]})
    if user.role == 'admin' and params.get('owner'):
        qs = qs.filter(owner_id=positive_id(params['owner'], '提交人参数无效。'))
    if params.get('q'):
        keyword = params['q'][:200]
        number = keyword.upper().removeprefix('REQ-').lstrip('0')
        query = Q(title__icontains=keyword) | Q(description__icontains=keyword) | Q(events__body__icontains=keyword)
        identifier = positive_id(number)
        if identifier is not None:
            query |= Q(pk=identifier)
        qs = qs.filter(query).distinct()
    scope = params.get('scope')
    if scope == 'archive':
        qs = qs.filter(status='done')
    elif scope == 'attention':
        qs = qs.exclude(status='done').filter(Q(next_actor=user.role) | Q(unread=True))
    if params.get('tab') == 'unread':
        qs = qs.filter(unread=True)
    elif params.get('tab') == 'active':
        qs = qs.exclude(status='done')
    schedule = params.get('schedule', '')
    today = timezone.localdate(timezone=ZoneInfo('Asia/Shanghai'))
    if schedule == 'unassessed':
        qs = qs.filter(status__in=['pending', 'in-progress'], estimated_completion_date__isnull=True)
    elif schedule == 'today':
        qs = qs.filter(status__in=['pending', 'in-progress'], estimated_completion_date=today)
    elif schedule == 'overdue':
        qs = qs.filter(status='in-progress', estimated_completion_date__lt=today)
    elif schedule:
        raise ValidationError('排期筛选无效。')
    order = params.get('sort', '')
    if order in ['estimate_asc', 'estimate_desc']:
        field = F('estimated_completion_date')
        qs = qs.order_by(field.asc(nulls_last=True) if order == 'estimate_asc' else field.desc(nulls_last=True), '-updated_at', '-id')
    elif order:
        raise ValidationError('排序方式无效。')
    return qs

def event(ticket, actor, kind, **values):
    return Event.objects.create(ticket=ticket, actor=actor, actor_name=actor.name, actor_role=actor.role, kind=kind, version=ticket.version, **values)

def notify_for_event(item):
    actor, ticket = item.actor, item.ticket
    if item.kind == 'created' or actor.role == 'consultant':
        recipients = User.objects.filter(role='admin', is_active=True).exclude(pk=actor.pk)
        Delivery.objects.create(event=item, channel='group_admin')
    else:
        recipients = User.objects.filter(pk=ticket.owner_id, is_active=True).exclude(pk=actor.pk)
        if recipients.exists():
            Delivery.objects.create(event=item, channel='group_consultant', recipient=ticket.owner)
    Notification.objects.bulk_create([Notification(recipient=u, event=item) for u in recipients])

def save_files(item, uploads, saved):
    for file, name, content_type in uploads:
        attachment = Attachment(event=item, original_name=name, size=file.size, content_type=content_type)
        attachment.file.save(name, file, save=False)
        saved.append((attachment.file.storage, attachment.file.name))
        attachment.save()

def submission_fingerprint(data, uploads):
    payload = {key: data.get(key, default) for key, default in {
        'title': '', 'system': '', 'module': '', 'kind': '', 'priority': 'medium',
        'due_date': None, 'description': '',
    }.items()}
    payload['files'] = []
    for file, name, content_type in uploads:
        digest = hashlib.sha256()
        try:
            for chunk in file.chunks():
                digest.update(chunk)
        finally:
            file.seek(0)
        payload['files'].append({'name': name, 'size': file.size, 'type': content_type, 'sha256': digest.hexdigest()})
    # File order carries no business meaning; bytes and normalized names do.
    payload['files'].sort(key=lambda item: (item['name'], item['sha256']))
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), default=str).encode()).hexdigest()


def create_ticket(user, data, files):
    if user.role != 'consultant':
        raise PermissionDenied('请使用顾问账号提交需求。')
    if data.get('system') not in {code for code, _ in SYSTEMS}:
        raise ValidationError('请选择 SAP 系统。')
    uploads = validate_uploads(files)
    fingerprint = submission_fingerprint(data, uploads)
    saved = []
    try:
        with transaction.atomic():
            User.objects.select_for_update().get(pk=user.pk)
            existing = Ticket.objects.filter(owner=user, request_key=data['request_key']).first()
            if existing:
                if not existing.submission_fingerprint or existing.submission_fingerprint != fingerprint:
                    raise Conflict({'detail': f'此提交编号已创建 {existing.number}，本次内容未保存。请打开原工单，在时间线补充。',
                                    'ticket_id': str(existing.pk), 'code': 'submission_content_mismatch'})
                return existing, False
            ticket = Ticket.objects.create(owner=user, submission_fingerprint=fingerprint, **data)
            item = event(ticket, user, 'created', body='提交了需求工单', to_status='pending')
            save_files(item, uploads, saved)
            notify_for_event(item)
            return ticket, True
    except Exception:
        for storage, name in saved:
            storage.delete(name)
        raise

def reply_ticket(user, ticket_id, data, files):
    uploads = validate_uploads(files)
    saved = []
    try:
        with transaction.atomic():
            qs = Ticket.objects.select_for_update()
            if user.role != 'admin':
                qs = qs.filter(owner=user)
            ticket = qs.get(pk=ticket_id)
            if data['version'] != ticket.version:
                raise Conflict()
            if 'status' in data:
                raise ValidationError('回复不再变更状态，请刷新页面后使用独立的状态操作。')
            if ticket.status == 'done':
                raise ValidationError('工单已完成，请先重新打开再回复。')
            ticket.version += 1
            ticket.updated_at = timezone.now()
            ticket.save(update_fields=['version', 'updated_at'])
            item = event(ticket, user, 'reply', body=data['body'])
            from .ai import enqueue_after_event
            enqueue_after_event(ticket, item)
            save_files(item, uploads, saved)
            notify_for_event(item)
            return ticket
    except Exception:
        for storage, name in saved:
            storage.delete(name)
        raise

def scoped_notifications(user):
    qs = Notification.objects.filter(recipient=user).select_related('event__ticket', 'event__actor')
    if user.role != 'admin':
        qs = qs.filter(event__ticket__owner=user)
    return qs.order_by('-id')

def apply_estimate(ticket, value, reason):
    previous = ticket.estimated_completion_date
    if value and value.year >= 9999:
        raise ValidationError('开发预计完成日期超出支持范围。')
    if value and value != previous and value < timezone.localdate(timezone=ZoneInfo('Asia/Shanghai')):
        raise ValidationError('开发预计完成日期不能早于今天。')
    if (previous is not None or ticket.estimate_needs_review) and not reason:
        raise ValidationError('调整、清空或重新确认排期时，请填写原因。')
    ticket.estimated_completion_date = value
    ticket.estimate_needs_review = False
    return {
        'previous_estimated_completion_date': previous,
        'estimated_completion_date': value,
    }


def estimate_description(previous, value):
    return f"开发预计完成日期：{previous or '待评估'} → {value or '待评估'}"


def change_ticket_schedule(user, ticket_id, data):
    if user.role != 'admin':
        raise PermissionDenied('只有管理员可以维护开发排期。')
    with transaction.atomic():
        ticket = Ticket.objects.select_for_update().get(pk=ticket_id)
        if data['version'] != ticket.version:
            raise Conflict()
        if ticket.status == 'done':
            raise ValidationError('工单已完成，请先重新打开再调整排期。')
        value = data['estimated_completion_date']
        if value == ticket.estimated_completion_date and not ticket.estimate_needs_review:
            return ticket
        reason = data.get('reason', '').strip()
        values = apply_estimate(ticket, value, reason)
        ticket.version += 1
        ticket.updated_at = timezone.now()
        ticket.save(update_fields=['estimated_completion_date', 'estimate_needs_review', 'version', 'updated_at'])
        body = estimate_description(values['previous_estimated_completion_date'], value)
        item = event(ticket, user, 'schedule', body=body + ('；原因：' + reason if reason else ''), **values)
        from .scheduling import reconcile_expedite
        reconcile_expedite(ticket, user, reason)
        from .ai import refresh_after_schedule
        refresh_after_schedule(ticket)
        notify_for_event(item)
        return ticket


# Each action has an explicit role, source state and destination.
ACTIONS = {
    'start': ('admin', ['pending'], 'in-progress', '开始处理', False),
    'submit': ('admin', ['in-progress'], 'waiting', '提交验收', True),
    'complete': ('admin', ['pending', 'in-progress', 'waiting'], 'done', '直接完成', True),
    'confirm': ('consultant', ['waiting'], 'done', '确认完成', False),
    'reject': ('consultant', ['waiting'], 'in-progress', '仍需处理', True),
    'reopen': (None, ['done'], 'in-progress', '重新打开', True),
}

def change_ticket_status(user, ticket_id, data):
    with transaction.atomic():
        qs = Ticket.objects.select_for_update()
        if user.role != 'admin':
            qs = qs.filter(owner=user)
        ticket = qs.get(pk=ticket_id)
        if data['version'] != ticket.version:
            raise Conflict()
        role, sources, target, label, needs_reason = ACTIONS[data['action']]
        if role and user.role != role:
            raise PermissionDenied('当前账号不能执行此操作。')
        if ticket.status not in sources:
            raise ValidationError('当前状态不能执行此操作，请刷新工单。')
        reason = data.get('reason', '').strip()
        if needs_reason and not reason:
            raise ValidationError('请填写处理结果或操作原因。')
        estimate_values = {}
        if 'estimated_completion_date' in data:
            if data['action'] != 'start':
                raise ValidationError('请使用独立的排期操作调整预计日期。')
            if data['estimated_completion_date'] != ticket.estimated_completion_date:
                estimate_values = apply_estimate(ticket, data['estimated_completion_date'], reason)
        previous = ticket.status
        if data['action'] in ['reject', 'reopen']:
            ticket.estimate_needs_review = True
        elif target in ['waiting', 'done']:
            ticket.estimate_needs_review = False
        ticket.status = target
        ticket.version += 1
        ticket.updated_at = timezone.now()
        ticket.next_actor = 'none' if target == 'done' else 'consultant' if target == 'waiting' else 'admin'
        ticket.save(update_fields=['status', 'version', 'updated_at', 'next_actor', 'estimated_completion_date', 'estimate_needs_review'])
        body = label + ('：' + reason if reason else '')
        if estimate_values:
            body += '\n' + estimate_description(estimate_values['previous_estimated_completion_date'], ticket.estimated_completion_date)
        item = event(ticket, user, 'status', from_status=previous, to_status=target, body=body, **estimate_values)
        from .scheduling import reconcile_expedite
        reconcile_expedite(ticket, user, reason)
        from .ai import enqueue_after_event
        enqueue_after_event(ticket, item)
        notify_for_event(item)
        return ticket
