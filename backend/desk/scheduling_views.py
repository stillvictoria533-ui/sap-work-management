from django.db.models import Case, When, Value, IntegerField, F, Q, Prefetch
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ExpediteRequest, Ticket
from .pagination import Pagination
from .permissions import Admin
from .serializers import TicketDetailSerializer
from .services import scoped_tickets
from .identifiers import positive_id
from .scheduling import create_expedite, reject_expedite, preview_batch, commit_batch, EDITABLE
from .scheduling_serializers import (ExpediteInput, ExpediteDecisionInput, ExpediteRequestSerializer,
                                     QueueTicketSerializer, BatchScheduleInput)


def scoped_requests(user):
    qs = ExpediteRequest.objects.select_related('ticket', 'requester', 'handled_by')
    return qs if user.role == 'admin' else qs.filter(requester=user, ticket__owner=user)


class TicketExpedite(APIView):
    def post(self, request, pk):
        get_object_or_404(scoped_tickets(request.user), pk=pk)
        serializer = ExpediteInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        _, created = create_expedite(request.user, pk, serializer.validated_data)
        ticket = scoped_tickets(request.user).prefetch_related('events__attachments').get(pk=pk)
        return Response(TicketDetailSerializer(ticket).data, status=201 if created else 200)


class ExpediteRequests(APIView):
    def get(self, request):
        qs = scoped_requests(request.user)
        state = request.query_params.get('status', 'pending')
        if state != 'all':
            if state not in dict(ExpediteRequest._meta.get_field('status').choices):
                raise ValidationError('加急申请状态无效。')
            qs = qs.filter(status=state)
        qs = qs.order_by('created_at', 'id')
        paginator = Pagination()
        return paginator.get_paginated_response(ExpediteRequestSerializer(paginator.paginate_queryset(qs, request), many=True).data)


class ExpediteDetail(APIView):
    def get(self, request, pk):
        return Response(ExpediteRequestSerializer(get_object_or_404(scoped_requests(request.user), pk=pk)).data)


class ExpediteDecision(APIView):
    permission_classes = [Admin]

    def post(self, request, pk):
        get_object_or_404(scoped_requests(request.user), pk=pk)
        serializer = ExpediteDecisionInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = reject_expedite(request.user, pk, serializer.validated_data)
        return Response(ExpediteRequestSerializer(scoped_requests(request.user).get(pk=result.pk)).data)


class DevelopmentQueue(APIView):
    permission_classes = [Admin]

    def get(self, request):
        qs = scoped_tickets(request.user).filter(status__in=EDITABLE).prefetch_related(
            Prefetch('expedite_requests', queryset=ExpediteRequest.objects.filter(status='pending').select_related('requester', 'handled_by'), to_attr='pending_expedites'))
        if request.query_params.get('q'):
            keyword = request.query_params['q'][:120]
            numeric = keyword.upper().removeprefix('REQ-').lstrip('0')
            condition = Q(title__icontains=keyword)
            identifier = positive_id(numeric)
            if identifier is not None:
                condition |= Q(pk=identifier)
            qs = qs.filter(condition)
        focus = request.query_params.get('focus_ticket')
        if focus:
            identifier = positive_id(focus, '工单参数无效。')
            qs = qs.annotate(focus=Case(When(pk=identifier, then=Value(0)), default=Value(1), output_field=IntegerField())).order_by('focus', F('estimated_completion_date').asc(nulls_last=True), 'id')
        else:
            qs = qs.order_by(F('estimated_completion_date').asc(nulls_last=True), 'id')
        paginator = Pagination()
        return paginator.get_paginated_response(QueueTicketSerializer(paginator.paginate_queryset(qs, request), many=True).data)


class BatchPreview(APIView):
    permission_classes = [Admin]

    def post(self, request):
        serializer = BatchScheduleInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(preview_batch(request.user, serializer.validated_data))


class BatchCommit(APIView):
    permission_classes = [Admin]

    def post(self, request):
        serializer = BatchScheduleInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(commit_batch(request.user, serializer.validated_data))
