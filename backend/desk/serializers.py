from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers
from .models import User, Ticket, Event, Attachment, Notification, Delivery, MODULES, SYSTEMS, TYPES, PRIORITIES, STATUSES

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'name', 'email', 'role', 'is_active', 'must_change_password', 'dingtalk_user_id', 'dingtalk_mobile', 'last_login', 'date_joined']

class AccountInput(serializers.Serializer):
    name = serializers.CharField(max_length=60)
    email = serializers.EmailField(max_length=254)
    role = serializers.ChoiceField(choices=User._meta.get_field('role').choices)
    dingtalk_mobile = serializers.RegexField(r'^1[3-9][0-9]{9}$', allow_blank=True, required=False, error_messages={'invalid': '请填写钉钉绑定的 11 位中国大陆手机号。'})
    dingtalk_user_id = serializers.RegexField(r'^[A-Za-z0-9_.:@-]{1,128}$', allow_blank=True, required=False)

    def validate_email(self, value):
        return value.strip().lower()

class PasswordInput(serializers.Serializer):
    current_password = serializers.CharField(max_length=256, allow_blank=True, default='')
    password = serializers.CharField(max_length=256, trim_whitespace=False)

    def validate_password(self, value):
        try:
            validate_password(value, self.context['request'].user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return value

class TicketInput(serializers.Serializer):
    request_key = serializers.UUIDField()
    title = serializers.CharField(max_length=120)
    system = serializers.ChoiceField(choices=SYSTEMS)
    module = serializers.ChoiceField(choices=MODULES)
    kind = serializers.ChoiceField(choices=TYPES)
    priority = serializers.ChoiceField(choices=PRIORITIES, default='medium')
    due_date = serializers.DateField(required=False, allow_null=True)
    description = serializers.CharField(max_length=10000)

    def validate_due_date(self, value):
        if value and value < timezone.localdate():
            raise serializers.ValidationError('期望完成日期不能早于今天。')
        return value

class ReplyInput(serializers.Serializer):
    body = serializers.CharField(max_length=10000)
    version = serializers.IntegerField(min_value=1)
    status = serializers.CharField(required=False)

    def validate(self, attrs):
        if 'status' in attrs:
            raise serializers.ValidationError('回复不再变更状态，请刷新页面后使用独立的状态操作。')
        return attrs

class ScheduleInput(serializers.Serializer):
    estimated_completion_date = serializers.DateField(allow_null=True)
    version = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class StatusInput(serializers.Serializer):
    action = serializers.ChoiceField(choices=['start', 'submit', 'complete', 'confirm', 'reject', 'reopen'])
    estimated_completion_date = serializers.DateField(required=False, allow_null=True)
    version = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=10000, required=False, allow_blank=True)

class AttachmentSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()
    class Meta:
        model = Attachment
        fields = ['id', 'original_name', 'size', 'url']
    def get_url(self, obj):
        return f'/api/attachments/{obj.pk}/download'

class EventSerializer(serializers.ModelSerializer):
    attachments = AttachmentSerializer(many=True)
    class Meta:
        model = Event
        fields = ['id', 'actor_name', 'actor_role', 'kind', 'body', 'is_ai', 'from_status', 'to_status', 'version', 'created_at', 'attachments', 'previous_estimated_completion_date', 'estimated_completion_date']

class TicketSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source='owner.name')
    number = serializers.CharField()
    unread = serializers.BooleanField(read_only=True, default=False)
    class Meta:
        model = Ticket
        fields = ['id', 'number', 'title', 'system', 'module', 'kind', 'priority', 'due_date', 'estimated_completion_date', 'estimate_needs_review', 'description', 'status', 'next_actor', 'version', 'owner_id', 'owner_name', 'created_at', 'updated_at', 'unread']

class TicketDetailSerializer(TicketSerializer):
    events = EventSerializer(many=True)
    ai_followup = serializers.SerializerMethodField()
    expedite_requests = serializers.SerializerMethodField()

    def get_expedite_requests(self, obj):
        from .scheduling_serializers import ExpediteRequestSerializer
        qs = obj.expedite_requests.select_related('ticket', 'requester', 'handled_by').order_by('-id')[:5]
        return ExpediteRequestSerializer(qs, many=True).data

    def get_ai_followup(self, obj):
        from .ai import ticket_ai_state
        return ticket_ai_state(obj)

    class Meta(TicketSerializer.Meta):
        fields = TicketSerializer.Meta.fields + ['events', 'ai_followup', 'expedite_requests']

class CalendarTicketSerializer(TicketSerializer):
    class Meta(TicketSerializer.Meta):
        fields = ['id', 'number', 'title', 'module', 'priority', 'status', 'owner_id', 'owner_name', 'created_at', 'estimated_completion_date', 'estimate_needs_review']

class NotificationSerializer(serializers.ModelSerializer):
    ticket_id = serializers.IntegerField(source='event.ticket_id')
    number = serializers.CharField(source='event.ticket.number')
    title = serializers.CharField(source='event.ticket.title')
    actor = serializers.CharField(source='event.actor_name')
    kind = serializers.CharField(source='event.kind')
    body = serializers.CharField(source='event.body')
    class Meta:
        model = Notification
        fields = ['id', 'ticket_id', 'number', 'title', 'actor', 'kind', 'body', 'read_at', 'created_at']

class DeliverySerializer(serializers.ModelSerializer):
    ticket_number = serializers.CharField(source='event.ticket.number')
    recipient_name = serializers.CharField(source='recipient.name', default='管理员群')
    class Meta:
        model = Delivery
        fields = ['id', 'channel', 'recipient_name', 'ticket_number', 'state', 'attempts', 'last_error', 'created_at', 'accepted_at', 'next_attempt_at']
