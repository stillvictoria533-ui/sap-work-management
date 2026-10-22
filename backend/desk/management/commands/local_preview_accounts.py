import secrets
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from desk.models import User

class Command(BaseCommand):
    help = '仅在空的本地开发库建立两个验收账号；随机密码写入权限 0600 的文件。'
    def handle(self, *args, **options):
        if not settings.DEBUG or settings.DATABASES['default']['ENGINE'] != 'django.db.backends.sqlite3':
            raise CommandError('仅允许本地 DEBUG + SQLite 环境。')
        if User.objects.exists():
            raise CommandError('已有账号，拒绝覆盖。')
        path = Path(settings.BASE_DIR).parent / '.local-credentials.txt'
        if path.exists():
            raise CommandError('本地凭据文件已存在，请先确认其归属，不自动覆盖。')
        lines = ['仅用于本地验收；禁止复制到生产环境。', '']
        for email, name, role in [('admin@local.test', '本地管理员', 'admin'), ('consultant@local.test', '验收顾问', 'consultant')]:
            password = secrets.token_urlsafe(18)
            User.objects.create_user(username=email, email=email, name=name, role=role, password=password, must_change_password=False)
            lines.extend([name, email, password, ''])
        with path.open('x', encoding='utf-8') as stream:
            path.chmod(0o600)
            stream.write('\n'.join(lines))
        self.stdout.write(f'本地账号已建立；凭据仅保存在 {path}')
