import uuid
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

MODULES = [(x, x) for x in ['FI', 'CO', 'MM', 'SD', 'PP', 'HR', '其他']]
SYSTEMS = [('DS4', 'DS4 / 110（开发）'), ('QS4', 'QS4 / 120（测试）'), ('PS4', 'PS4 / 100（生产）')]
TYPES = [(x, x) for x in ['配置', '开发', '报表', '接口', '问题', '其他']]
PRIORITIES = [('low', '低'), ('medium', '中'), ('high', '高'), ('urgent', '紧急')]
STATUSES = [('pending', '待受理'), ('in-progress', '处理中'), ('waiting', '待验收'), ('done', '已完成')]

class User(AbstractUser):
    username = models.CharField(max_length=254, unique=True)
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=60)
    role = models.CharField(max_length=12, choices=[('admin', '管理员'), ('consultant', '顾问')], default='consultant')
    must_change_password = models.BooleanField(default=True)
    session_version = models.PositiveIntegerField(default=1)
    dingtalk_mobile = models.CharField(max_length=11, blank=True)
    dingtalk_user_id = models.CharField(max_length=128, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(Lower('email'), name='unique_lower_email'), models.UniqueConstraint(fields=['dingtalk_user_id'], condition=~models.Q(dingtalk_user_id=''), name='unique_bound_dingtalk_user'), models.UniqueConstraint(fields=['dingtalk_mobile'], condition=~models.Q(dingtalk_mobile=''), name='unique_bound_dingtalk_mobile')]

class Ticket(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='tickets')
    request_key = models.UUIDField(default=uuid.uuid4)
    submission_fingerprint = models.CharField(max_length=64, blank=True, default='')
    title = models.CharField(max_length=120)
    system = models.CharField(max_length=3, choices=SYSTEMS, blank=True, default='')
    module = models.CharField(max_length=10, choices=MODULES)
    kind = models.CharField(max_length=10, choices=TYPES)
    priority = models.CharField(max_length=10, choices=PRIORITIES, default='medium')
    due_date = models.DateField(null=True, blank=True)
    estimated_completion_date = models.DateField(null=True, blank=True, db_index=True)
    estimate_needs_review = models.BooleanField(default=False)
    description = models.TextField(max_length=10000)
    status = models.CharField(max_length=20, choices=STATUSES, default='pending')
    next_actor = models.CharField(max_length=12, default='admin')
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-updated_at', '-id']
        constraints = [models.UniqueConstraint(fields=['owner', 'request_key'], name='unique_submission_key')]
        indexes = [models.Index(fields=['owner', 'status', '-updated_at']), models.Index(fields=['status', 'module', 'priority'])]

    @property
    def number(self):
        return f'REQ-{self.pk:05d}'

class Event(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.PROTECT, related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    actor_name = models.CharField(max_length=60)
    actor_role = models.CharField(max_length=12)
    kind = models.CharField(max_length=20, choices=[('created', '创建'), ('reply', '回复'), ('status', '状态变更'), ('schedule', '开发排期'), ('expedite', '加急申请')])
    body = models.TextField(max_length=10000, blank=True)
    is_ai = models.BooleanField(default=False)
    previous_estimated_completion_date = models.DateField(null=True, blank=True)
    estimated_completion_date = models.DateField(null=True, blank=True)
    schedule_batch = models.ForeignKey('ScheduleBatch', null=True, blank=True, on_delete=models.PROTECT, related_name='events')
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    version = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['id']


class ExpediteRequest(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.PROTECT, related_name='expedite_requests')
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='expedite_requests')
    request_key = models.UUIDField()
    requested_date = models.DateField()
    reason = models.TextField(max_length=2000)
    status = models.CharField(max_length=12, choices=[('pending', '待评估'), ('approved', '已安排'), ('rejected', '未通过'), ('cancelled', '已结束')], default='pending')
    version = models.PositiveIntegerField(default=1)
    handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='handled_expedites')
    handled_at = models.DateTimeField(null=True)
    decision_note = models.TextField(max_length=2000, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['ticket'], condition=models.Q(status='pending'), name='one_pending_expedite_per_ticket'), models.UniqueConstraint(fields=['requester', 'request_key'], name='unique_expedite_request_key')]
        indexes = [models.Index(fields=['status', 'created_at'])]


class ScheduleBatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    request_key = models.UUIDField()
    fingerprint = models.CharField(max_length=64)
    reason = models.TextField(max_length=2000)
    source_request = models.ForeignKey(ExpediteRequest, null=True, on_delete=models.PROTECT, related_name='schedule_batches')
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['actor', 'request_key'], name='unique_schedule_batch_key')]


class TicketAIRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(Ticket, on_delete=models.PROTECT, related_name='ai_runs')
    trigger = models.ForeignKey(Event, on_delete=models.PROTECT, related_name='ai_triggers')
    accepted_event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name='ai_acceptances')
    context_version = models.PositiveIntegerField()
    accepted_scope = models.JSONField(default=dict)
    status = models.CharField(max_length=16, default='queued')
    lease_token = models.UUIDField(default=uuid.uuid4)
    lease_expires_at = models.DateTimeField(default=timezone.now)
    deadline_at = models.DateTimeField(null=True)
    attempts = models.PositiveIntegerField(default=0)
    reason = models.CharField(max_length=40, blank=True)
    retry_after = models.DateTimeField(null=True)
    reply_event = models.OneToOneField(Event, null=True, on_delete=models.PROTECT, related_name='ai_run')
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['ticket', 'trigger'], name='unique_ai_ticket_trigger')]
        indexes = [models.Index(fields=['status', 'lease_expires_at'])]


class TicketAIExecutorState(models.Model):
    """Only bounded health metadata; never store credentials or ticket text."""
    name = models.CharField(max_length=16, primary_key=True, default='primary', editable=False)
    created_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_scan_at = models.DateTimeField(null=True, blank=True)
    capabilities = models.JSONField(default=dict)


class UploadQuotaLock(models.Model):
    """Serialize capacity checks and attachment writes across all API workers."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='one_upload_quota_lock')]


class TicketAIQuery(models.Model):
    """A consumed, per-run query budget entry and executor-owned evidence receipt."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(TicketAIRun, on_delete=models.PROTECT, related_name='queries')
    request_key = models.UUIDField()
    context_version = models.PositiveIntegerField()
    lease_token = models.UUIDField()
    plan = models.JSONField()
    plan_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=12, default='reserved')
    deadline_at = models.DateTimeField()
    result_hash = models.CharField(max_length=64, blank=True)
    error_code = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['run', 'request_key'], name='unique_ai_query_request')]

def upload_path(instance, filename):
    return f'{instance.event.ticket_id}/{uuid.uuid4().hex}'

class Attachment(models.Model):
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name='attachments')
    file = models.FileField(upload_to=upload_path)
    original_name = models.CharField(max_length=200)
    size = models.PositiveIntegerField()
    content_type = models.CharField(max_length=100)

class Notification(models.Model):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='notifications')
    event = models.ForeignKey(Event, on_delete=models.PROTECT)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['recipient', 'event'], name='unique_event_notification')]
        indexes = [models.Index(fields=['recipient', 'read_at', '-id'])]

class Delivery(models.Model):
    event = models.ForeignKey(Event, on_delete=models.PROTECT)
    channel = models.CharField(max_length=20, choices=[('group_admin', '群内 @ 管理员'), ('group_consultant', '群内 @ 顾问'), ('group', '旧版管理员群'), ('personal', '旧版顾问个人')])
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    schedule_batch = models.ForeignKey(ScheduleBatch, null=True, blank=True, on_delete=models.PROTECT)
    state = models.CharField(max_length=15, default='pending')
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    locked_at = models.DateTimeField(null=True)
    lock_token = models.UUIDField(null=True)
    last_error = models.CharField(max_length=160, blank=True)
    provider_reference = models.CharField(max_length=256, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    accepted_at = models.DateTimeField(null=True)

    class Meta:
        indexes = [models.Index(fields=['state', 'next_attempt_at'])]
        constraints = [models.UniqueConstraint(fields=['schedule_batch', 'recipient'], condition=models.Q(schedule_batch__isnull=False), name='one_batch_delivery_per_recipient')]

class Audit(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action = models.CharField(max_length=40)
    target = models.CharField(max_length=100)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

class LoginBucket(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    count = models.PositiveIntegerField(default=0)
    since = models.DateTimeField(default=timezone.now)
