import json
import time

from django.core.serializers.json import DjangoJSONEncoder
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from desk.models import User
from desk.permissions import Admin
from desk.pagination import Pagination
from .models import ACTIVE_STATUSES, SYSTEMS
from .services import (conversation_data, create_conversation, provider_state,
                       retry_turn, scoped_conversations, send_turn, turn_data)


class AssistantAdminView(APIView):
    permission_classes = [Admin]


class ConversationInput(serializers.Serializer):
    request_key = serializers.UUIDField()
    system = serializers.ChoiceField(choices=SYSTEMS, default='DS4')
    ticket_id = serializers.IntegerField(min_value=1, allow_null=True, required=False)


class QuestionInput(serializers.Serializer):
    request_key = serializers.UUIDField()
    message = serializers.CharField(max_length=12000, trim_whitespace=True)


class RetryInput(serializers.Serializer):
    attempt = serializers.IntegerField(min_value=0)


class Status(AssistantAdminView):
    def get(self, request):
        return Response({'provider': provider_state(), 'sap': {'connected': False, 'status': '未接通',
                         'systems': [{'id': key, 'label': label} for key, label in SYSTEMS]},
                         'max_message_length': 12000})


class Conversations(AssistantAdminView):
    def get(self, request):
        paginator = Pagination()
        page = paginator.paginate_queryset(scoped_conversations(request.user), request)
        return paginator.get_paginated_response([conversation_data(item) for item in page])

    def post(self, request):
        data = ConversationInput(data=request.data)
        data.is_valid(raise_exception=True)
        item, created = create_conversation(request.user, data.validated_data)
        return Response(conversation_data(item, True), status=201 if created else 200)


class ConversationDetail(AssistantAdminView):
    def get(self, request, pk):
        item = get_object_or_404(scoped_conversations(request.user).prefetch_related('turns'), pk=pk)
        return Response(conversation_data(item, True))


class Questions(AssistantAdminView):
    def post(self, request, pk):
        data = QuestionInput(data=request.data)
        data.is_valid(raise_exception=True)
        turn, created = send_turn(request.user, pk, data.validated_data)
        return Response(turn_data(turn), status=201 if created else 200)


class Retry(AssistantAdminView):
    def post(self, request, pk, turn_id):
        data = RetryInput(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(turn_data(retry_turn(request.user, pk, turn_id, data.validated_data['attempt'])))


class Cancel(AssistantAdminView):
    def post(self, request, pk, turn_id):
        item = get_object_or_404(scoped_conversations(request.user), pk=pk)
        turn = get_object_or_404(item.turns, pk=turn_id)
        item.turns.filter(pk=turn.pk, status__in=ACTIVE_STATUSES).update(
            status='cancelled', error_code='cancelled', finished_at=timezone.now(), updated_at=timezone.now())
        turn.refresh_from_db()
        return Response(turn_data(turn))


def sse(event, data):
    return f'event: {event}\ndata: {json.dumps(data, cls=DjangoJSONEncoder, ensure_ascii=False)}\n\n'


class EventStreamRenderer(JSONRenderer):
    # Negotiation must accept EventSource's Accept header before APIView.get runs.
    # Successful responses use StreamingHttpResponse; HTTP error bodies remain JSON.
    media_type = 'text/event-stream'
    format = 'event-stream'


class Stream(AssistantAdminView):
    renderer_classes = [JSONRenderer, EventStreamRenderer]

    def get(self, request, pk, turn_id):
        item = get_object_or_404(scoped_conversations(request.user), pk=pk)
        get_object_or_404(item.turns, pk=turn_id)
        user_id, version = request.user.pk, request.user.session_version

        def events():
            yield 'retry: 2000\n\n'
            started, previous, heartbeat = time.monotonic(), '', 0
            while time.monotonic() - started < 25:
                owner = User.objects.filter(pk=user_id, role='admin', is_active=True, must_change_password=False, session_version=version).first()
                if not owner or not scoped_conversations(owner).filter(pk=pk).exists():
                    yield sse('revoked', {'detail': '登录状态或访问权限已变化，请重新登录。'})
                    return
                turn = item.turns.get(pk=turn_id)
                payload = sse('snapshot', turn_data(turn))
                if payload != previous:
                    yield payload
                    previous = payload
                if turn.status not in ACTIVE_STATUSES:
                    yield sse('end', {'status': turn.status})
                    return
                if time.monotonic() - heartbeat >= 5:
                    yield ': heartbeat\n\n'
                    heartbeat = time.monotonic()
                time.sleep(0.5)

        response = StreamingHttpResponse(events(), content_type='text/event-stream; charset=utf-8')
        response['Cache-Control'] = 'no-store'
        response['X-Accel-Buffering'] = 'no'
        return response
