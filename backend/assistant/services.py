from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError, Throttled

from desk.errors import Conflict
from desk.models import Audit, User
from desk.services import scoped_tickets
from .models import ACTIVE_STATUSES, Conversation, Turn, WorkerState


ERRORS = {
    'not_configured': 'AI 服务尚未配置，你的问题已保存。配置完成后可以重试。',
    'codex_missing': 'Codex CLI 尚未安装或当前版本不兼容，你的问题已保存。',
    'codex_login': 'Codex CLI 尚未登录或登录已失效，请联系管理员。',
    'worker_offline': 'AI 处理服务暂未就绪，你的问题已保存，稍后可以重试。',
    'provider_auth': 'AI 服务的账号或模型配置不可用，请联系管理员。',
    'provider_limit': 'AI 服务暂时达到调用或额度限制，请稍后重试。',
    'provider_error': 'AI 服务暂时不可用，本次没有生成完整回答。',
    'provider_timeout': 'AI 服务响应超时，本次没有生成完整回答。',
    'incomplete': '回答未完整生成，已保留收到的部分内容。',
    'interrupted': '本次生成因服务中断而停止，已保留收到的内容，请手动重试。',
    'access_changed': '账号或关联工单的访问权限已变化，本次生成已停止。',
    'cancelled': '已停止生成。',
}


def scoped_conversations(user):
    if user.role != 'admin':
        return Conversation.objects.none()
    qs = Conversation.objects.filter(owner=user)
    if user.role != 'admin':
        qs = qs.filter(Q(ticket__isnull=True) | Q(ticket__owner=user))
    return qs.select_related('ticket')


def provider_state():
    # Only the worker owns model credentials. The API must never invoke the CLI
    # or mount its authentication volume to answer readiness requests.
    worker = WorkerState.objects.filter(name='chat').first()
    matching = bool(worker and worker.provider_kind == settings.AI_PROVIDER)
    online = bool(matching and worker.heartbeat_at >= timezone.now() - timedelta(seconds=90))
    configured = bool(matching and worker.provider_configured)
    code = ('not_configured' if settings.AI_PROVIDER == 'disabled' else
            'worker_offline' if not online else worker.provider_code if not configured else '')
    return {'configured': configured, 'worker_online': online, 'ready': configured and online,
            'code': code, 'message': ERRORS.get(code, ''), 'model': worker.provider_model if matching else '',
            'name': worker.provider_name if matching else 'Codex CLI' if settings.AI_PROVIDER == 'codex' else 'AI 服务'}


def turn_data(turn):
    return {name: getattr(turn, name) for name in [
        'id', 'input_text', 'output_text', 'status', 'error_code', 'attempt', 'created_at', 'updated_at',
        'started_at', 'finished_at', 'model', 'context_meta',
    ]} | {'error_message': ERRORS.get(turn.error_code, '')}


def conversation_data(item, detail=False):
    data = {name: getattr(item, name) for name in ['id', 'title', 'system', 'created_at', 'updated_at']}
    data['ticket'] = ({'id': item.ticket_id, 'number': item.ticket.number, 'title': item.ticket.title} if item.ticket_id else None)
    if detail:
        data['turns'] = [turn_data(t) for t in item.turns.all()]
    return data


def create_conversation(user, data):
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        ticket = get_object_or_404(scoped_tickets(user), pk=data['ticket_id']) if data.get('ticket_id') else None
        item = scoped_conversations(user).filter(request_key=data['request_key']).first()
        if item:
            if item.system != data['system'] or item.ticket_id != (ticket.pk if ticket else None):
                raise Conflict('重复请求的对话设置不同，请重新创建。')
            return item, False
        item = Conversation.objects.create(owner=user, ticket=ticket, system=data['system'], request_key=data['request_key'])
        Audit.objects.create(actor=user, action='chat_created', target=str(item.pk))
        return item, True


def check_budget(user, conversation):
    if Turn.objects.filter(conversation__owner=user, status__in=ACTIVE_STATUSES).exists():
        raise Conflict('你还有一个问题正在处理，请完成或停止后再发送。')
    if conversation.turns.count() >= 100:
        raise ValidationError('此对话已达到 100 个问题，请新建对话继续。')
    recent = Turn.objects.filter(conversation__owner=user, created_at__gte=timezone.now() - timedelta(days=1))
    # Count attempts too, so retrying cannot bypass the daily provider-call budget.
    from django.db.models import Sum
    if recent.count() >= settings.AI_TURNS_PER_DAY or (recent.aggregate(total=Sum('attempt'))['total'] or 0) >= settings.AI_TURNS_PER_DAY:
        raise Throttled(detail='今天的 AI 对话次数已达到上限，请稍后再试。')


def enqueue_values(user):
    state = provider_state()
    return {'status': 'queued' if state['ready'] else 'blocked', 'error_code': state['code'],
            'session_version': user.session_version, 'model': state['model'],
            'updated_at': timezone.now(), 'finished_at': None, 'started_at': None, 'worker_token': None}


def send_turn(user, pk, data):
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        item = get_object_or_404(scoped_conversations(user), pk=pk)
        existing = item.turns.filter(request_key=data['request_key']).first()
        if existing:
            if existing.input_text != data['message']:
                raise Conflict('重复请求的问题内容不同，请刷新对话后重试。')
            return existing, False
        check_budget(user, item)
        values = enqueue_values(user)
        turn = Turn.objects.create(conversation=item, request_key=data['request_key'], input_text=data['message'],
                                   attempt=1 if values['status'] == 'queued' else 0, **values)
        if item.title == '新对话':
            item.title = ' '.join(data['message'].split())[:60]
        item.updated_at = timezone.now()
        item.save(update_fields=['title', 'updated_at'])
        Audit.objects.create(actor=user, action='chat_question_saved', target=str(turn.pk), detail={'status': turn.status})
        return turn, True


def retry_turn(user, pk, turn_id, attempt):
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        item = get_object_or_404(scoped_conversations(user), pk=pk)
        turn = get_object_or_404(item.turns, pk=turn_id)
        if turn.attempt != attempt or turn.status not in ['blocked', 'failed', 'cancelled']:
            raise Conflict('此问题的处理状态已变化，请刷新后再试。')
        if item.turns.order_by('-created_at', '-id').first().pk != turn.pk:
            raise ValidationError('仅支持重试最后一个问题。较早的问题请重新发送。')
        check_budget(user, item)
        values = enqueue_values(user)
        if values['status'] == 'blocked':
            turn.error_code = values['error_code']
            turn.save(update_fields=['error_code'])
            return turn
        for key, value in values.items():
            setattr(turn, key, value)
        turn.attempt += 1
        turn.output_text, turn.usage, turn.context_meta = '', {}, {}
        turn.save()
        item.updated_at = timezone.now()
        item.save(update_fields=['updated_at'])
        Audit.objects.create(actor=user, action='chat_retried', target=str(turn.pk), detail={'attempt': turn.attempt})
        return turn
