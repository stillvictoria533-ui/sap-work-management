from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from assistant.models import WorkerState


class Command(BaseCommand):
    help = 'Check worker heartbeat. Provider login readiness is shown separately in the app.'

    def handle(self, *args, **options):
        if not WorkerState.objects.filter(name='chat', heartbeat_at__gte=timezone.now()-timedelta(seconds=90)).exists():
            raise CommandError('Chat worker heartbeat expired')
