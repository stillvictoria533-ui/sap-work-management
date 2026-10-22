"""Opt-in DS4 pilot broker. Browsers own jobs; devices can only claim their own jobs.

No SAP credentials, endpoint selection, shell strings or model tool calls cross this API.
"""
import hashlib
import json
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, NotFound, ValidationError, Throttled
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from desk.errors import Conflict
from desk.permissions import Admin
from desk.models import Audit, User
from .models import ConnectorDevice, ConnectorPairing, SapQuery

MAX_RESULT_BYTES = 96 * 1024
ADAPTER_OPERATIONS = {'sap-eyes-mac': ['tcode', 'ddic'], 'sap-gui-windows': ['gui_session']}
ERRORS = {
    'declined': '已在本机拒绝本次查询。',
    'sap_failed': 'DS4 查询失败，请检查本机工具箱连接和权限。',
    'timeout': '查询超时；请确认连接器仍运行，重新查询不会自动重试旧任务。',
    'invalid_evidence': '结果的系统、对象或格式校验失败，未接受结果。',
    'output_limit': '结果超过原型大小限制，请缩小查询对象。',
    'revoked': '设备已断开，本次查询已停止。',
    'gui_unavailable': '未能访问 SAP GUI Scripting，请检查 SAP GUI 是否已登录、脚本组件及服务端权限。',
    'gui_session_missing': '没有可读取的 DS4/110 会话；请先在 SAP GUI 登录。',
    'gui_session_ambiguous': '发现多个 DS4/110 会话，请只保留一个用于本次测试的会话。',
    'gui_busy': 'SAP GUI 正在通信或画面已变化，请稳定在目标画面后重试。',
    'gui_endpoint_unverified': 'GUI 会话的服务器或系统编号未通过固定 DS4 校验，请联系维护人员确认连接配置。',
}


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def enabled():
    if not settings.SAP_CONNECTOR_ENABLED:
        raise NotFound('本机连接器尚未启用。')


def active_devices(user):
    if user.role != 'admin':
        return ConnectorDevice.objects.none()
    return ConnectorDevice.objects.filter(owner=user, session_version=user.session_version,
        revoked_at__isnull=True, expires_at__gt=timezone.now())


def expire_queries():
    SapQuery.objects.filter(status__in=['queued', 'running'], expires_at__lte=timezone.now()).update(
        status='failed', error_code='timeout', finished_at=timezone.now())


def device_data(device):
    return {'id': device.pk, 'name': device.name, 'system': 'DS4', 'client': '110',
            'adapter': device.adapter, 'operations': ADAPTER_OPERATIONS.get(device.adapter, []),
            'online': device.last_seen_at >= timezone.now() - timedelta(seconds=45),
            'last_seen_at': device.last_seen_at, 'last_success_at': device.last_success_at,
            'expires_at': device.expires_at}


def query_data(query):
    return {'id': query.pk, 'device_id': query.device_id, 'system': 'DS4', 'client': '110',
            'operation': query.operation, 'object_name': query.object_name, 'status': query.status,
            'created_at': query.created_at, 'finished_at': query.finished_at,
            'error_message': ERRORS.get(query.error_code, ''), 'result': query.result}


class StrictInput(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise ValidationError({'detail': '包含未支持的字段。'})
        return super().to_internal_value(data)


class PairInput(StrictInput):
    code = serializers.RegexField(r'^[A-Za-z0-9_-]{32}$')
    name = serializers.CharField(max_length=60, default='我的 Mac')
    adapter = serializers.ChoiceField(choices=list(ADAPTER_OPERATIONS), default='sap-eyes-mac')


class QueryInput(StrictInput):
    request_key = serializers.UUIDField()
    device_id = serializers.UUIDField()
    operation = serializers.ChoiceField(choices=['tcode', 'ddic', 'gui_session'])
    object_name = serializers.RegexField(r'^[A-Z][A-Z0-9_]{0,29}$', max_length=30)


class ResultInput(StrictInput):
    lease = serializers.RegexField(r'^[A-Za-z0-9_-]{43}$')
    result = serializers.JSONField(required=False)
    error_code = serializers.ChoiceField(choices=list(ERRORS), required=False)

    def validate(self, data):
        if ('result' in data) == ('error_code' in data):
            raise ValidationError('提交结果或错误之一。')
        return data


def parse(serializer, request):
    value = serializer(data=request.data)
    value.is_valid(raise_exception=True)
    return value.validated_data


def validate_evidence(result, query):
    if not isinstance(result, dict) or len(json.dumps(result, ensure_ascii=False).encode()) > MAX_RESULT_BYTES:
        raise ValidationError('结果过大或格式无效。')
    meta = result.get('_evidence')
    expected = {'trust': 'UNTRUSTED_EVIDENCE', 'instructionAuthority': False,
                'system': 'DS4', 'client': '110', 'command': query.operation, 'object': query.object_name}
    if not isinstance(meta, dict) or any(type(meta.get(k)) != type(v) or meta.get(k) != v for k, v in expected.items()):
        raise ValidationError('证据身份与本次查询不匹配。')
    # This validates the envelope, not SAP cryptographic attestation. The paired
    # local adapter is trusted to perform the existing double identity checks.
    if not re.fullmatch(r'[0-9a-f]{64}', str(meta.get('contentSha256', ''))):
        raise ValidationError('缺少内容校验值。')
    if query.operation == 'tcode' and result.get('tcode') != query.object_name:
        raise ValidationError('事务码结果不匹配。')
    if query.operation == 'gui_session':
        expected_keys = {'transaction', 'program', 'screen', 'source', '_evidence'}
        if set(result) != expected_keys or result.get('source') != 'SAP GUI session metadata':
            raise ValidationError('Windows 适配仅接受当前会话元数据。')
        if set(meta) != set(expected) | {'contentSha256'}:
            raise ValidationError('Windows 证据不能附加账号、服务器或画面内容。')
        for key in ['transaction', 'program', 'screen']:
            if not isinstance(result[key], str) or len(result[key]) > 128 or any(ord(c) < 32 for c in result[key]):
                raise ValidationError('会话元数据格式无效。')
        payload = {key: value for key, value in result.items() if key != '_evidence'}
        if digest(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))) != meta['contentSha256']:
            raise ValidationError('会话元数据校验值不匹配。')
    return result


class AdminConnectorView(APIView):
    permission_classes = [Admin]


class ConnectorStatus(AdminConnectorView):
    def get(self, request):
        if not settings.SAP_CONNECTOR_ENABLED:
            return Response({'enabled': False, 'devices': [], 'queries': []})
        expire_queries()
        return Response({'enabled': True, 'devices': [device_data(d) for d in active_devices(request.user)],
            'queries': [query_data(q) for q in SapQuery.objects.filter(owner=request.user).order_by('-created_at')[:10]]})


class PairCode(AdminConnectorView):
    def post(self, request):
        enabled()
        now = timezone.now()
        with transaction.atomic():
            User.objects.select_for_update().get(pk=request.user.pk)
            if active_devices(request.user).count() >= 3:
                raise ValidationError('最多配对 3 台设备，请先断开旧设备。')
            ConnectorPairing.objects.filter(owner=request.user, consumed_at__isnull=True).update(consumed_at=now)
            code = secrets.token_urlsafe(24)
            ConnectorPairing.objects.create(code_hash=digest(code), owner=request.user,
                session_version=request.user.session_version, expires_at=now + timedelta(minutes=10))
        return Response({'code': code, 'expires_at': now + timedelta(minutes=10)}, status=201)


class PairDevice(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        enabled()
        data = parse(PairInput, request)
        now = timezone.now()
        with transaction.atomic():
            pairing = ConnectorPairing.objects.select_for_update().filter(code_hash=digest(data['code']),
                consumed_at__isnull=True, expires_at__gt=now).first()
            if not pairing:
                raise AuthenticationFailed('配对码无效、已使用或已过期。')
            owner = User.objects.select_for_update().get(pk=pairing.owner_id)
            if owner.role != 'admin' or not owner.is_active or owner.must_change_password or owner.session_version != pairing.session_version:
                raise AuthenticationFailed('账号状态已变化。')
            if active_devices(owner).count() >= 3:
                raise ValidationError('已达到设备数量限制。')
            # Conditional update also protects single-use consumption on SQLite.
            if not ConnectorPairing.objects.filter(pk=pairing.pk, consumed_at__isnull=True).update(consumed_at=now):
                raise Conflict('配对码已使用。')
            token = secrets.token_urlsafe(32)
            device = ConnectorDevice.objects.create(owner=owner, name=data['name'], token_hash=digest(token),
                adapter=data['adapter'],
                session_version=owner.session_version, expires_at=now + timedelta(hours=24))
            Audit.objects.create(actor=owner, action='connector_paired', target=str(device.pk))
        return Response({'device': device_data(device), 'token': token,
                         'account': owner.name, 'protocol': 'sapdesk-ds4-v1'}, status=201)


class RevokeDevice(AdminConnectorView):
    def post(self, request, pk):
        enabled()
        with transaction.atomic():
            device = get_object_or_404(ConnectorDevice.objects.select_for_update(), pk=pk, owner=request.user)
            device.revoked_at = timezone.now()
            device.save(update_fields=['revoked_at'])
            SapQuery.objects.filter(device=device, status__in=['queued', 'running']).update(
                status='failed', error_code='revoked', finished_at=timezone.now())
            Audit.objects.create(actor=request.user, action='connector_revoked', target=str(device.pk))
        return Response({'revoked': True})


class Queries(AdminConnectorView):
    def post(self, request):
        enabled()
        data = parse(QueryInput, request)
        now = timezone.now()
        with transaction.atomic():
            User.objects.select_for_update().get(pk=request.user.pk)
            device = get_object_or_404(active_devices(request.user).select_for_update(), pk=data['device_id'])
            if data['operation'] not in ADAPTER_OPERATIONS.get(device.adapter, []):
                raise ValidationError('此设备不支持这种查询，请选择设备支持的操作。')
            if data['operation'] == 'gui_session' and data['object_name'] != 'CURRENT':
                raise ValidationError('Windows GUI 原型只能读取 CURRENT 当前会话。')
            old = SapQuery.objects.filter(owner=request.user, request_key=data['request_key']).first()
            if old:
                if (old.device_id, old.operation, old.object_name) != (device.pk, data['operation'], data['object_name']):
                    raise Conflict('重复请求的查询内容不同。')
                return Response(query_data(old))
            expire_queries()
            if device.last_seen_at < now - timedelta(seconds=45):
                raise Conflict('本机连接器已离线，请重新启动。')
            if SapQuery.objects.filter(device=device, status__in=['queued', 'running']).exists():
                raise Conflict('此设备已有查询待完成，请查看连接器终端。')
            if SapQuery.objects.filter(owner=request.user, created_at__gte=now-timedelta(days=1)).count() >= 100:
                raise Throttled(detail='本机查询每天最多 100 次。')
            query = SapQuery.objects.create(owner=request.user, device=device, request_key=data['request_key'],
                operation=data['operation'], object_name=data['object_name'], expires_at=now + timedelta(minutes=4))
            Audit.objects.create(actor=request.user, action='sap_query_created', target=str(query.pk),
                detail={'device': str(device.pk), 'system': 'DS4', 'operation': query.operation, 'object': query.object_name})
        return Response(query_data(query), status=201)


class QueryDetail(AdminConnectorView):
    def get(self, request, pk):
        enabled()
        expire_queries()
        return Response(query_data(get_object_or_404(SapQuery, pk=pk, owner=request.user)))


class DeviceAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        return 'Bearer'

    def authenticate(self, request):
        enabled()
        header = request.headers.get('Authorization', '')
        if not re.fullmatch(r'Bearer [A-Za-z0-9_-]{43}', header):
            raise AuthenticationFailed('缺少设备凭据。')
        device = ConnectorDevice.objects.select_related('owner').filter(token_hash=digest(header[7:]),
            revoked_at__isnull=True, expires_at__gt=timezone.now()).first()
        if not device or device.owner.role != 'admin' or not device.owner.is_active or device.owner.must_change_password or device.session_version != device.owner.session_version:
            raise AuthenticationFailed('设备授权失效，请重新配对。')
        return device.owner, device


class AgentView(APIView):
    authentication_classes = [DeviceAuthentication]
    permission_classes = [IsAuthenticated]

    def locked_device(self, request):
        # Recheck authorization under the same lock used for revoke/claim/result.
        owner = User.objects.select_for_update().get(pk=request.user.pk)
        device = active_devices(owner).select_for_update().filter(pk=request.auth.pk).first()
        if not device or not owner.is_active or owner.must_change_password:
            raise AuthenticationFailed('设备授权失效。')
        device.last_seen_at = timezone.now()
        device.save(update_fields=['last_seen_at'])
        return device


class Heartbeat(AgentView):
    def post(self, request):
        with transaction.atomic():
            self.locked_device(request)
        return Response({'ok': True})


class Claim(AgentView):
    def post(self, request):
        with transaction.atomic():
            device = self.locked_device(request)
            expire_queries()
            query = SapQuery.objects.filter(device=device, status='queued').order_by('created_at').first()
            if not query:
                return Response({'job': None})
            lease = secrets.token_urlsafe(32)
            changed = SapQuery.objects.filter(pk=query.pk, status='queued').update(status='running', lease_hash=digest(lease))
            if not changed:
                return Response({'job': None})
        return Response({'job': {'id': query.pk, 'system': 'DS4', 'client': '110', 'operation': query.operation,
            'object_name': query.object_name, 'expires_at': query.expires_at, 'lease': lease}})


class Complete(AgentView):
    def post(self, request, pk):
        data = parse(ResultInput, request)
        with transaction.atomic():
            device = self.locked_device(request)
            query = get_object_or_404(SapQuery.objects.select_for_update(), pk=pk, device=device)
            if not secrets.compare_digest(query.lease_hash, digest(data['lease'])):
                raise AuthenticationFailed('查询凭据不匹配。')
            if query.status in ['completed', 'failed'] and query.finished_at:
                raise Conflict('查询已结束，不能覆盖结果。')
            if query.status != 'running' or query.expires_at <= timezone.now():
                raise Conflict('查询已过期或已停止。')
            if 'result' in data:
                query.result = validate_evidence(data['result'], query)
                query.status = 'completed'
                device.last_success_at = timezone.now()
                device.save(update_fields=['last_success_at'])
            else:
                query.status, query.error_code = 'failed', data['error_code']
            query.finished_at = timezone.now()
            query.save(update_fields=['result', 'status', 'error_code', 'finished_at'])
            Audit.objects.create(actor=request.user, action='sap_query_finished', target=str(query.pk),
                detail={'status': query.status, 'system': 'DS4', 'operation': query.operation, 'object': query.object_name})
        return Response({'accepted': True})


def evidence_for_turn(user, conversation, query_id):
    if not query_id:
        return None
    enabled()
    if conversation.system != 'DS4':
        raise ValidationError('本机证据仅可用于 DS4 对话。')
    query = get_object_or_404(SapQuery, pk=query_id, owner=user, status='completed',
        device__in=active_devices(user), finished_at__gte=timezone.now()-timedelta(minutes=30))
    return query
