"""JSON-only coordinator entry point; intentionally excludes operator get/retry."""
import json
import sys
from django.core.management.base import BaseCommand, CommandError
from rest_framework.exceptions import ValidationError
from desk.ai_gateway import dispatch
from desk.models import Ticket, TicketAIRun, TicketAIQuery


class Command(BaseCommand):
    help = '工单 AI 固定命令代理；不执行模型、SAP、Shell 或任意管理命令。'

    def handle(self, *args, **options):
        try:
            raw = sys.stdin.buffer.read(65537) if hasattr(sys.stdin, 'buffer') else sys.stdin.read(65537)
            if len(raw) > 65536: raise ValueError()
            result = dispatch(json.loads(raw))
            self.stdout.write(json.dumps(result, ensure_ascii=False, allow_nan=False))
        except (ValueError, TypeError, KeyError, ValidationError, Ticket.DoesNotExist, TicketAIRun.DoesNotExist, TicketAIQuery.DoesNotExist):
            raise CommandError('AI_GATEWAY_REJECTED') from None
