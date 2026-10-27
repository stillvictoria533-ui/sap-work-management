import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


SYSTEMS = [('DS4', 'DS4 / 110 · 开发'), ('QS4', 'QS4 / 120 · 测试')]
ACTIVE_STATUSES = ['queued', 'running']


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    request_key = models.UUIDField(default=uuid.uuid4)
    title = models.CharField(max_length=100, default='新对话')
    system = models.CharField(max_length=8, choices=SYSTEMS, default='DS4')
    ticket = models.ForeignKey('desk.Ticket', null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-updated_at', '-id']
        constraints = [models.UniqueConstraint(fields=['owner', 'request_key'], name='unique_chat_creation')]
        indexes = [models.Index(fields=['owner', '-updated_at'])]


class Turn(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, related_name='turns', on_delete=models.CASCADE)
    request_key = models.UUIDField()
    input_text = models.TextField(max_length=12000)
    output_text = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=[(s, s) for s in ['queued', 'running', 'completed', 'blocked', 'failed', 'cancelled']])
    error_code = models.CharField(max_length=40, blank=True)
    attempt = models.PositiveIntegerField(default=0)
    session_version = models.PositiveIntegerField()
    model = models.CharField(max_length=100, blank=True)
    worker_token = models.UUIDField(null=True)
    usage = models.JSONField(default=dict)
    context_meta = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ['created_at', 'id']
        constraints = [
            models.UniqueConstraint(fields=['conversation', 'request_key'], name='unique_chat_send'),
            models.UniqueConstraint(fields=['conversation'], condition=models.Q(status__in=ACTIVE_STATUSES), name='one_active_chat_turn'),
        ]
        indexes = [models.Index(fields=['status', 'created_at'])]


class WorkerState(models.Model):
    name = models.CharField(primary_key=True, max_length=40, default='chat')
    heartbeat_at = models.DateTimeField(default=timezone.now)
    provider_kind = models.CharField(max_length=16, default='disabled')
    provider_configured = models.BooleanField(default=False)
    provider_code = models.CharField(max_length=40, default='not_configured')
    provider_name = models.CharField(max_length=40, default='AI 服务')
    provider_model = models.CharField(max_length=100, blank=True)
