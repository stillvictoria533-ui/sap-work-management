from datetime import timedelta
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone
from desk.models import LoginBucket

class Command(BaseCommand):
    help = '清理过期登录限流记录和会话；不删除工单、通知或审计记录。'
    def handle(self, *args, **options):
        LoginBucket.objects.filter(since__lt=timezone.now() - timedelta(days=1)).delete()
        call_command('clearsessions')
        self.stdout.write('维护完成。')
