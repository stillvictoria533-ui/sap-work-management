import csv
import hashlib
import json
import re
import secrets
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.conf import settings
from django.db import transaction, connection, IntegrityError
from django.db.models import Q, Count, F
from django.db.models.functions import TruncDate
from django.http import JsonResponse, HttpResponse, FileResponse, Http404
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied
from .models import User, Ticket, Event, Attachment, Notification, Delivery, Audit, LoginBucket, ExpediteRequest
from .permissions import Admin, SignedIn
from .pagination import Pagination
from .serializers import UserSerializer, AccountInput, PasswordInput, TicketInput, ReplyInput, StatusInput, ScheduleInput, TicketSerializer, TicketDetailSerializer, CalendarTicketSerializer, NotificationSerializer, DeliverySerializer
from .services import scoped_tickets, filtered_tickets, create_ticket, reply_ticket, change_ticket_status, change_ticket_schedule, scoped_notifications

@require_GET
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        return JsonResponse({'status': 'ok'})
    except Exception:
        return JsonResponse({'status': 'unavailable'}, status=503)

@require_GET
@ensure_csrf_cookie
def session(request):
    user = request.user
    valid = user.is_authenticated and user.is_active and request.session.get('version') == user.session_version
    if user.is_authenticated and not valid:
        logout(request)
    return JsonResponse({'user': UserSerializer(user).data if valid else None, 'csrf_token': get_token(request)})

def login_budget(email, ip):
    account_key = hashlib.sha256(('account:' + email).encode()).hexdigest()
    for raw, limit in [(account_key, 10), (hashlib.sha256(('ip:' + ip).encode()).hexdigest(), 120)]:
        with transaction.atomic():
            bucket, _ = LoginBucket.objects.select_for_update().get_or_create(key=raw)
            if bucket.since < timezone.now() - timedelta(minutes=15):
                bucket.count, bucket.since = 0, timezone.now()
            if bucket.count >= limit:
                return None
            bucket.count += 1
            bucket.save()
    return account_key

@require_POST
@csrf_protect
def sign_in(request):
    try:
        data = json.loads(request.body)
        email, password = str(data.get('email', '')).strip().lower(), str(data.get('password', ''))
        if len(email) > 254 or len(password) > 256 or not email or not password:
            raise ValueError()
    except (ValueError, AttributeError):
        return JsonResponse({'detail': '请填写正确的账号和密码。'}, status=400)
    ip = request.META.get('HTTP_X_REAL_IP') if settings.TRUST_PROXY else request.META.get('REMOTE_ADDR')
    key = login_budget(email, ip or 'unknown')
    if key is None:
        response = JsonResponse({'detail': '登录尝试过于频繁，请 15 分钟后重试。'}, status=429)
        response['Retry-After'] = '900'
        return response
    user = authenticate(request, username=email, password=password)
    if not user:
        return JsonResponse({'detail': '账号、密码有误或账号已停用。'}, status=401)
    login(request, user)
    request.session['version'] = user.session_version
    LoginBucket.objects.filter(key=key).delete()
    return JsonResponse({'user': UserSerializer(user).data, 'csrf_token': get_token(request)})

class SignOut(APIView):
    permission_classes = [SignedIn]
    def post(self, request):
        logout(request)
        return Response({'detail': '已退出登录。'})

class Password(APIView):
    permission_classes = [SignedIn]
    def post(self, request):
        serializer = PasswordInput(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = request.user
        data = serializer.validated_data
        if not user.must_change_password and not user.check_password(data['current_password']):
            raise ValidationError('当前密码不正确。')
        if user.check_password(data['password']):
            raise ValidationError('新密码不能与旧密码相同。')
        with transaction.atomic():
            user.set_password(data['password'])
            user.must_change_password = False
            user.session_version += 1
            user.save(update_fields=['password', 'must_change_password', 'session_version'])
            Audit.objects.create(actor=user, action='password_changed', target=str(user.pk))
        update_session_auth_hash(request, user)
        request.session['version'] = user.session_version
        return Response({'user': UserSerializer(user).data, 'csrf_token': get_token(request)})

class TicketList(APIView):
    def get(self, request):
        # Tabs count the current scope and filters, before choosing a tab or page.
        count_params = {**request.query_params.dict(), 'tab': 'all', 'sort': ''}
        tab_counts = filtered_tickets(request.user, count_params).order_by().aggregate(
            total=Count('pk', distinct=True),
            active=Count('pk', filter=~Q(status='done'), distinct=True),
            unread_tickets=Count('pk', filter=Q(unread=True), distinct=True),
        )
        qs = filtered_tickets(request.user, request.query_params)
        paginator = Pagination()
        page = paginator.paginate_queryset(qs, request)
        response = paginator.get_paginated_response(TicketSerializer(page, many=True).data)
        response.data['tab_counts'] = tab_counts
        return response
    def post(self, request):
        serializer = TicketInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        ticket, created = create_ticket(request.user, serializer.validated_data, request.FILES.getlist('files'))
        return Response(TicketDetailSerializer(scoped_tickets(request.user).prefetch_related('events__attachments').get(pk=ticket.pk)).data, status=201 if created else 200)

class TicketDetail(APIView):
    def get(self, request, pk):
        ticket = get_object_or_404(scoped_tickets(request.user).prefetch_related('events__attachments'), pk=pk)
        return Response(TicketDetailSerializer(ticket).data)

class DevelopmentCalendar(APIView):
    permission_classes = [Admin]

    def get(self, request):
        zone = ZoneInfo('Asia/Shanghai')
        today = timezone.localdate(timezone=zone)
        month = request.query_params.get('month', today.strftime('%Y-%m'))
        try:
            if not re.fullmatch(r'[1-9][0-9]{3}-(0[1-9]|1[0-2])', month):
                raise ValueError()
            first = date.fromisoformat(month + '-01')
            if first.year >= 9999:
                raise ValueError()
            following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
        except (ValueError, OverflowError):
            raise ValidationError('月份格式应为 YYYY-MM，请选择有效月份。')
        start = datetime.combine(first, time.min, tzinfo=zone)
        end = datetime.combine(following, time.min, tzinfo=zone)
        mode = request.query_params.get('mode', 'submitted')
        if mode not in ['submitted', 'estimated']:
            raise ValidationError('日历视图无效。')
        base = scoped_tickets(request.user)
        if mode == 'estimated':
            tickets = base.filter(estimated_completion_date__gte=first, estimated_completion_date__lt=following)
            days_query = tickets.annotate(day=F('estimated_completion_date'))
        else:
            tickets = base.filter(created_at__gte=start, created_at__lt=end)
            days_query = tickets.annotate(day=TruncDate('created_at', tzinfo=zone))
        days = list(days_query.values('day').annotate(
            count=Count('pk'), urgent_count=Count('pk', filter=Q(priority='urgent'))).order_by('day'))
        selected = request.query_params.get('date')
        if selected:
            try:
                if not re.fullmatch(r'[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}', selected):
                    raise ValueError()
                selected = date.fromisoformat(selected)
                if not first <= selected < following:
                    raise ValueError()
            except ValueError:
                raise ValidationError('日期无效或不属于当前月份。')
        else:
            selected = today if first <= today < following else days[0]['day'] if days else first
        day_start = datetime.combine(selected, time.min, tzinfo=zone)
        daily = (tickets.filter(estimated_completion_date=selected) if mode == 'estimated' else
                 tickets.filter(created_at__gte=day_start, created_at__lt=day_start + timedelta(days=1))).order_by('created_at', 'id')
        paginator = Pagination()
        page = paginator.paginate_queryset(daily, request)
        response = paginator.get_paginated_response(CalendarTicketSerializer(page, many=True).data)
        response.data.update(month=month, date=selected.isoformat(), mode=mode, timezone='Asia/Shanghai',
                             unassessed_count=base.filter(status__in=['pending', 'in-progress'], estimated_completion_date__isnull=True).count(),
                             month_count=sum(day['count'] for day in days),
                             days=[{'date': day['day'].isoformat(), 'count': day['count'],
                                    'urgent_count': day['urgent_count']} for day in days])
        return response

class TicketReply(APIView):
    def post(self, request, pk):
        get_object_or_404(scoped_tickets(request.user), pk=pk)
        serializer = ReplyInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        reply_ticket(request.user, pk, serializer.validated_data, request.FILES.getlist('files'))
        return Response(TicketDetailSerializer(scoped_tickets(request.user).prefetch_related('events__attachments').get(pk=pk)).data)

class TicketStatus(APIView):
    def post(self, request, pk):
        get_object_or_404(scoped_tickets(request.user), pk=pk)
        serializer = StatusInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        change_ticket_status(request.user, pk, serializer.validated_data)
        return Response(TicketDetailSerializer(scoped_tickets(request.user).prefetch_related('events__attachments').get(pk=pk)).data)

class TicketSchedule(APIView):
    permission_classes = [Admin]

    def post(self, request, pk):
        get_object_or_404(scoped_tickets(request.user), pk=pk)
        serializer = ScheduleInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        change_ticket_schedule(request.user, pk, serializer.validated_data)
        return Response(TicketDetailSerializer(scoped_tickets(request.user).prefetch_related('events__attachments').get(pk=pk)).data)


class TicketRead(APIView):
    def post(self, request, pk):
        ticket = get_object_or_404(scoped_tickets(request.user), pk=pk)
        try:
            through = int(request.data.get('through_event', 0))
        except (ValueError, TypeError):
            raise ValidationError('已读位置无效。')
        get_object_or_404(Event, ticket=ticket, pk=through)
        scoped_notifications(request.user).filter(event__ticket=ticket, event_id__lte=through, read_at__isnull=True).update(read_at=timezone.now())
        return Response({'detail': '已标记为已读。'})

class Download(APIView):
    def get(self, request, pk):
        attachment = get_object_or_404(Attachment.objects.select_related('event__ticket'), pk=pk)
        if request.user.role != 'admin' and attachment.event.ticket.owner_id != request.user.pk:
            raise Http404()
        try:
            file = attachment.file.open('rb')
        except FileNotFoundError:
            raise Http404()
        return FileResponse(file, as_attachment=True, filename=attachment.original_name, content_type='application/octet-stream')

class Stats(APIView):
    def get(self, request):
        qs = scoped_tickets(request.user)
        return Response({'total': qs.count(), 'active': qs.exclude(status='done').count(), 'unread_tickets': qs.filter(unread=True).count(), 'attention': qs.exclude(status='done').filter(Q(next_actor=request.user.role) | Q(unread=True)).count(), 'notifications': scoped_notifications(request.user).filter(read_at__isnull=True).count(), 'expedite_pending': ExpediteRequest.objects.filter(status='pending', ticket__status__in=['pending', 'in-progress']).count() if request.user.role == 'admin' else 0})

class Notices(APIView):
    def get(self, request):
        paginator = Pagination()
        qs = scoped_notifications(request.user)
        if request.query_params.get('unread') == 'true':
            qs = qs.filter(read_at__isnull=True)
        return paginator.get_paginated_response(NotificationSerializer(paginator.paginate_queryset(qs, request), many=True).data)
    def post(self, request):
        scoped_notifications(request.user).filter(read_at__isnull=True).update(read_at=timezone.now())
        return Response({'detail': '已全部标记为已读。'})

def safe_csv(value):
    text = str(value if value is not None else '')
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')) else text

class Export(APIView):
    permission_classes = [Admin]
    def get(self, request):
        qs = filtered_tickets(request.user, request.query_params)
        if qs.count() > 10000:
            raise ValidationError('请缩小筛选范围至 10000 条工单以内。')
        mode = request.query_params.get('mode', 'tickets')
        if mode not in ['tickets', 'events']:
            raise ValidationError('导出类型无效。')
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="sap-desk-{mode}-{timezone.localdate()}.csv"'
        response.write('\ufeff')
        writer = csv.writer(response)
        if mode == 'tickets':
            writer.writerow(['编号', '标题', '系统', '模块', '类型', '优先级', '状态', '提交人', '期望完成日期', '开发预计完成日期', '排期需重新评估', '描述', '创建时间', '更新时间'])
            for t in qs.iterator():
                writer.writerow(map(safe_csv, [t.number, t.title, t.get_system_display() or '未指定', t.module, t.kind, t.get_priority_display(), t.get_status_display(), t.owner.name, t.due_date, t.estimated_completion_date, '是' if t.estimate_needs_review else '否', t.description, timezone.localtime(t.created_at).isoformat(), timezone.localtime(t.updated_at).isoformat()]))
        else:
            events = Event.objects.filter(ticket__in=qs).select_related('ticket')
            if events.count() > 50000:
                raise ValidationError('沟通记录超过 50000 条，请缩小筛选范围。')
            writer.writerow(['编号', '操作人', '角色', '操作', '内容', '原状态', '新状态', '操作时间'])
            for e in events.iterator():
                writer.writerow(map(safe_csv, [e.ticket.number, e.actor_name, e.actor_role, e.get_kind_display(), e.body, e.from_status, e.to_status, timezone.localtime(e.created_at).isoformat()]))
        Audit.objects.create(actor=request.user, action='export', target=mode, detail={'ticket_count': qs.count()})
        return response

class Accounts(APIView):
    permission_classes = [Admin]
    def get(self, request):
        return Response({'results': UserSerializer(User.objects.all().order_by('id'), many=True).data})
    def post(self, request):
        serializer = AccountInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        password = secrets.token_urlsafe(18)
        try:
            with transaction.atomic():
                user = User.objects.create_user(username=data['email'], password=password, **data)
                Audit.objects.create(actor=request.user, action='account_created', target=str(user.pk), detail={'email': user.email, 'role': user.role})
        except IntegrityError:
            raise ValidationError('此邮箱、钉钉手机号或用户 ID 已被其他账号使用。')
        return Response({'user': UserSerializer(user).data, 'temporary_password': password}, status=201)

class AccountDetail(APIView):
    permission_classes = [Admin]
    def patch(self, request, pk):
        operation = request.data.get('operation', 'edit')
        if operation not in ['edit', 'reset', 'enable', 'disable']:
            raise ValidationError('操作无效。')
        with transaction.atomic():
            # Lock administrators in a stable order before modifying access.
            list(User.objects.select_for_update().filter(role='admin').order_by('id'))
            user = get_object_or_404(User.objects.select_for_update(), pk=pk)
            if user.pk == request.user.pk and operation != 'edit':
                raise ValidationError('当前账号请在“我的账号”修改密码，不能停用自己。')
            before = {'name': user.name, 'email': user.email, 'role': user.role, 'is_active': user.is_active, 'dingtalk_user_id': user.dingtalk_user_id, 'dingtalk_mobile_bound': bool(user.dingtalk_mobile)}
            password = None
            if operation == 'edit':
                serializer = AccountInput(data=request.data)
                serializer.is_valid(raise_exception=True)
                data = serializer.validated_data
                if user.pk == request.user.pk and data['role'] != user.role:
                    raise ValidationError('不能修改自己的角色。')
                if User.objects.exclude(pk=pk).filter(email__iexact=data['email']).exists():
                    raise ValidationError('此邮箱已存在。')
                if data.get('dingtalk_user_id') and User.objects.exclude(pk=pk).filter(dingtalk_user_id=data['dingtalk_user_id']).exists():
                    raise ValidationError('此钉钉用户 ID 已绑定其他账号。')
                if data.get('dingtalk_mobile') and User.objects.exclude(pk=pk).filter(dingtalk_mobile=data['dingtalk_mobile']).exists():
                    raise ValidationError('此钉钉手机号已绑定其他账号。')
                for key, value in data.items():
                    setattr(user, key, value)
                user.username = user.email
            elif operation == 'reset':
                password = secrets.token_urlsafe(18)
                user.set_password(password)
                user.must_change_password = True
            else:
                user.is_active = operation == 'enable'
            if before['role'] == 'admin' and before['is_active'] and (user.role != 'admin' or not user.is_active) and not User.objects.exclude(pk=pk).filter(role='admin', is_active=True).exists():
                raise ValidationError('至少保留一个启用的管理员。')
            access_changed = before['role'] != user.role or before['is_active'] != user.is_active or operation == 'reset'
            if access_changed:
                user.session_version += 1
            try:
                with transaction.atomic():
                    user.save()
            except IntegrityError:
                raise ValidationError('此邮箱、钉钉手机号或用户 ID 已被其他账号使用。')
            after = {'name': user.name, 'email': user.email, 'role': user.role, 'is_active': user.is_active, 'dingtalk_user_id': user.dingtalk_user_id, 'dingtalk_mobile_bound': bool(user.dingtalk_mobile)}
            Audit.objects.create(actor=request.user, action='account_' + operation, target=str(pk), detail={'before': before, 'after': after})
        return Response({'user': UserSerializer(user).data, **({'temporary_password': password} if password else {})})

class AuditList(APIView):
    permission_classes = [Admin]
    def get(self, request):
        qs = Audit.objects.select_related('actor').order_by('-id')[:100]
        return Response({'results': [{'id': a.pk, 'actor': a.actor.name, 'action': a.action, 'target': a.target, 'detail': a.detail, 'created_at': a.created_at} for a in qs]})

class Deliveries(APIView):
    permission_classes = [Admin]
    def get(self, request):
        from .dingtalk import configuration_status
        qs = Delivery.objects.select_related('event__ticket', 'recipient').order_by('-id')
        paginator = Pagination()
        response = paginator.get_paginated_response(DeliverySerializer(paginator.paginate_queryset(qs, request), many=True).data)
        response.data['configuration'] = configuration_status()
        response.data['states'] = dict(Delivery.objects.values('state').annotate(n=Count('id')).values_list('state', 'n'))
        return response

class RetryDelivery(APIView):
    permission_classes = [Admin]
    def post(self, request, pk):
        with transaction.atomic():
            job = get_object_or_404(Delivery.objects.select_for_update(), pk=pk)
            if job.channel not in ['group_admin', 'group_consultant']:
                raise ValidationError('旧版通知仅保留记录，不再投递。')
            if job.state not in ['blocked', 'failed']:
                raise ValidationError('只能重试被阻止或失败的通知。')
            job.state, job.attempts, job.last_error = 'pending', 0, ''
            job.next_attempt_at, job.lock_token, job.locked_at = timezone.now(), None, None
            job.save()
            Audit.objects.create(actor=request.user, action='delivery_retry', target=str(pk))
        return Response({'detail': '已加入重试队列。'})
