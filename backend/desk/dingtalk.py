"""Signed shared group bot with targeted member mentions.

Acceptance by DingTalk is recorded as accepted, not proof of user delivery/read.
Provider URLs are fixed; never pass credentials or raw provider errors to logs/UI.
"""
import base64
import hashlib
import hmac
import time
import re
import uuid
from datetime import timedelta
from urllib.parse import urlparse
import requests
from django.conf import settings
from django.db import transaction, connection
from django.db.models import Q
from django.utils import timezone
from .models import Delivery, User

class DeliveryError(Exception):
    def __init__(self, message, retryable=True):
        super().__init__(message)
        self.retryable = retryable

def configuration_status():
    base = urlparse(settings.PUBLIC_BASE_URL)
    valid_url = base.scheme == 'https' and bool(base.hostname) and not base.username and not base.password and not base.query and not base.fragment
    return {'enabled': settings.DINGTALK_ENABLED, 'public_url_ready': valid_url,
            'group_ready': bool(settings.DINGTALK_GROUP_TOKEN and settings.DINGTALK_GROUP_SECRET),
            'unbound_admins': User.objects.filter(role='admin', is_active=True, dingtalk_mobile='').count(),
            'unbound_consultants': User.objects.filter(role='consultant', is_active=True, dingtalk_mobile='').count()}


def signed_parameters(token, secret, timestamp):
    signature = base64.b64encode(hmac.new(secret.encode(), f'{timestamp}\n{secret}'.encode(), hashlib.sha256).digest()).decode()
    return {'access_token': token, 'timestamp': str(timestamp), 'sign': signature}

class DingTalkClient:
    def __init__(self):
        self.http = requests.Session()
        self.http.trust_env = False

    def post(self, url, **kwargs):
        try:
            response = self.http.post(url, timeout=(5, 10), allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise DeliveryError('钉钉网络请求失败或超时；稍后重试可能产生重复通知。') from None
        if response.status_code != 200:
            raise DeliveryError(f'钉钉接口 HTTP {response.status_code}，请检查应用权限或稍后重试。', response.status_code >= 500 or response.status_code in [401, 429])
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError()
        except ValueError:
            raise DeliveryError('钉钉返回了无法识别的响应。') from None
        return result

    def send(self, job):
        config = configuration_status()
        if job.channel not in ['group_admin', 'group_consultant']:
            raise DeliveryError('旧版通知仅保留记录，不再投递；新更新使用群内定向 @。', False)
        if not config['enabled']:
            raise DeliveryError('钉钉通知尚未启用。', False)
        if not config['public_url_ready']:
            raise DeliveryError('尚未配置可供用户访问的 HTTPS 地址。', False)
        if not config['group_ready']:
            raise DeliveryError('未配置协作群机器人的 Token 与加签 Secret。', False)
        e = job.event
        if job.channel == 'group_admin':
            recipients = list(User.objects.filter(role='admin', is_active=True).exclude(pk=e.actor_id))
        else:
            recipients = list(User.objects.filter(pk=job.recipient_id, is_active=True))
            if job.recipient_id != e.ticket.owner_id:
                raise DeliveryError('通知接收人与工单提交人不一致，已阻止发送。', False)
        if not recipients:
            raise DeliveryError('没有启用的通知接收人。', False)
        if any(not re.fullmatch(r'1[3-9][0-9]{9}', u.dingtalk_mobile) for u in recipients):
            raise DeliveryError('接收人尚未绑定有效的钉钉手机号，请在账号管理中补充。', False)
        mobiles = sorted({u.dingtalk_mobile for u in recipients})
        # Mentions require both content and atMobiles; never include ticket/reply text.
        text = 'SAP Desk · ' + ' '.join('@' + mobile for mobile in mobiles)
        if job.schedule_batch_id:
            count = job.schedule_batch.events.filter(ticket__owner_id=job.recipient_id).count()
            text += f'\n你的 {count} 张工单排期有调整。'
            text += f'\n请登录查看日期及说明：{settings.PUBLIC_BASE_URL}/#/notifications'
        elif e.kind == 'expedite':
            text += '\n' + ('有新的工单加急申请，请评估目标日期与排期影响。' if job.channel == 'group_admin' else '你的加急申请有处理结果，请登录查看。')
            text += f'\n请登录查看：{settings.PUBLIC_BASE_URL}/#/tickets/{e.ticket_id}'
        else:
            text += '\n' + ('有新的需求或补充说明。' if job.channel == 'group_admin' else '你的工单有更新。')
            text += f'\n紧急程度：{e.ticket.get_priority_display()}'
            text += f'\n请登录查看：{settings.PUBLIC_BASE_URL}/#/tickets/{e.ticket_id}'
        result = self.post('https://oapi.dingtalk.com/robot/send',
                           params=signed_parameters(settings.DINGTALK_GROUP_TOKEN, settings.DINGTALK_GROUP_SECRET, int(time.time() * 1000)),
                           json={'msgtype': 'text', 'text': {'content': text}, 'at': {'atMobiles': mobiles, 'isAtAll': False}})
        if result.get('errcode') != 0:
            raise DeliveryError('群机器人拒绝了通知，请检查签名、关键词或频率限制。')
        return ''


def claim_job():
    now = timezone.now()
    with transaction.atomic():
        qs = Delivery.objects.filter(Q(state='pending', next_attempt_at__lte=now) | Q(state='sending', locked_at__lt=now - timedelta(minutes=5))).order_by('next_attempt_at', 'id')
        qs = qs.select_for_update(skip_locked=True) if connection.features.has_select_for_update_skip_locked else qs.select_for_update()
        job = qs.first()
        if not job:
            return None
        job.state, job.locked_at, job.lock_token = 'sending', now, uuid.uuid4()
        job.attempts += 1
        job.save(update_fields=['state', 'locked_at', 'lock_token', 'attempts'])
        return job.pk, job.lock_token

def deliver_one(client):
    claim = claim_job()
    if claim is None:
        return False
    pk, token = claim
    job = Delivery.objects.select_related('event__ticket', 'recipient', 'schedule_batch').get(pk=pk)
    try:
        reference = client.send(job)
        updates = {'state': 'accepted', 'accepted_at': timezone.now(), 'provider_reference': reference, 'last_error': ''}
    except DeliveryError as exc:
        state = 'blocked' if not exc.retryable else 'failed' if job.attempts >= 5 else 'pending'
        updates = {'state': state, 'last_error': str(exc), 'next_attempt_at': timezone.now() + timedelta(seconds=min(3600, 30 * 2 ** min(job.attempts, 7)))}
    Delivery.objects.filter(pk=pk, lock_token=token, state='sending').update(**updates, locked_at=None, lock_token=None)
    return True
