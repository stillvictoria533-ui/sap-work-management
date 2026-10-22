from rest_framework import serializers
from .models import ExpediteRequest
from .serializers import TicketSerializer


class ExpediteInput(serializers.Serializer):
    request_key = serializers.UUIDField()
    requested_date = serializers.DateField()
    reason = serializers.CharField(max_length=2000)
    version = serializers.IntegerField(min_value=1)


class ExpediteDecisionInput(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)
    ticket_version = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=2000)


class BatchChangeInput(serializers.Serializer):
    ticket_id = serializers.IntegerField(min_value=1)
    version = serializers.IntegerField(min_value=1)
    estimated_completion_date = serializers.DateField(allow_null=True)


class BatchScheduleInput(serializers.Serializer):
    request_key = serializers.UUIDField()
    reason = serializers.CharField(max_length=2000)
    changes = BatchChangeInput(many=True, min_length=1, max_length=100)
    expedite_request_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    expedite_request_version = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    preview_token = serializers.CharField(max_length=1000, required=False, allow_blank=True)

    def validate(self, attrs):
        if bool(attrs.get('expedite_request_id')) != bool(attrs.get('expedite_request_version')):
            raise serializers.ValidationError('请同时提供加急申请及其版本。')
        return attrs


class ExpediteRequestSerializer(serializers.ModelSerializer):
    ticket_number = serializers.CharField(source='ticket.number')
    ticket_title = serializers.CharField(source='ticket.title')
    ticket_version = serializers.IntegerField(source='ticket.version')
    current_estimated_completion_date = serializers.DateField(source='ticket.estimated_completion_date', allow_null=True)
    requester_name = serializers.CharField(source='requester.name')
    handled_by_name = serializers.CharField(source='handled_by.name', default=None)

    class Meta:
        model = ExpediteRequest
        fields = ['id', 'ticket_id', 'ticket_number', 'ticket_title', 'ticket_version', 'current_estimated_completion_date', 'requester_name',
                  'requested_date', 'reason', 'status', 'version', 'created_at', 'handled_at', 'handled_by_name', 'decision_note']


class QueueTicketSerializer(TicketSerializer):
    pending_expedite = serializers.SerializerMethodField()

    def get_pending_expedite(self, obj):
        return ExpediteRequestSerializer(obj.pending_expedites[0]).data if obj.pending_expedites else None

    class Meta(TicketSerializer.Meta):
        fields = TicketSerializer.Meta.fields + ['pending_expedite']
