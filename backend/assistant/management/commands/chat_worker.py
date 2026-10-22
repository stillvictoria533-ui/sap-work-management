import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from assistant.worker import process_one


class Command(BaseCommand):
    help = '运行独立聊天 worker；不会查询 SAP、扫描工单或发送通知。'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        self.stdout.write('聊天 worker 已启动。SAP 连接未启用。')
        try:
            while True:
                close_old_connections()
                worked = process_one()
                if options['once']:
                    return
                if not worked:
                    time.sleep(2)
        except KeyboardInterrupt:
            self.stdout.write('聊天 worker 已停止。')
