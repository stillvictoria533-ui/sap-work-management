import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from desk.dingtalk import DingTalkClient, deliver_one

class Command(BaseCommand):
    help = '处理持久化钉钉通知队列；部署单个 worker，按至少 4 秒的间隔调用。'
    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
    def handle(self, *args, **options):
        client = DingTalkClient()
        while True:
            close_old_connections()
            deliver_one(client)
            if options['once']:
                return
            time.sleep(4)
