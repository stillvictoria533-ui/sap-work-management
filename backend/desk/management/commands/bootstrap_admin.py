import getpass
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from desk.models import User

class Command(BaseCommand):
    help = '交互式创建首个管理员，密码不放入命令行或日志。'
    def add_arguments(self, parser):
        parser.add_argument('--email', required=True)
        parser.add_argument('--name', default='管理员')
    def handle(self, *args, **options):
        email = options['email'].strip().lower()
        if User.objects.filter(role='admin', is_active=True).exists():
            raise CommandError('已有管理员，请使用网页账号管理。')
        user = User(username=email, email=email, name=options['name'], role='admin', must_change_password=False)
        password = getpass.getpass('管理员密码（至少 12 字符）：')
        if password != getpass.getpass('再次输入密码：'):
            raise CommandError('两次密码不一致。')
        try:
            validate_password(password, user)
            user.full_clean(exclude=['password'])
        except ValidationError as exc:
            raise CommandError(str(exc))
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS('管理员已创建。'))
