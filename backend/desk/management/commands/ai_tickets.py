import json
import sys
import uuid
from django.core.management.base import BaseCommand,CommandError
from rest_framework.exceptions import ValidationError
from desk.ai import candidate_page,claim,publish,defer,renew,ticket_context,enabled,request_retry,request_blocked_followup
from desk.models import Ticket,TicketAIRun


class Command(BaseCommand):
    help='已受理工单 AI 跟进网关：清单、领取、续租、回写、受阻；不调用模型或 SAP。'

    def add_arguments(self,parser):
        sub=parser.add_subparsers(dest='operation',required=True)
        listing=sub.add_parser('list');listing.add_argument('--after-id',type=int,default=0);listing.add_argument('--limit',type=int,default=10)
        sub.add_parser('health')
        cont=sub.add_parser('continue');cont.add_argument('--ticket-id',type=int,required=True)
        cont.add_argument('--version',type=int,required=True);cont.add_argument('--request-key',type=uuid.UUID,required=True)
        for name in ['get','claim','retry']:
            item=sub.add_parser(name);item.add_argument('--ticket-id',type=int,required=True)
            if name in ['claim','retry']:item.add_argument('--version',type=int,required=True)
        for name in ['reply','defer','renew']:
            item=sub.add_parser(name);item.add_argument('--run-id',type=uuid.UUID,required=True)

    def handle(self,*args,**options):
        try:
            op=options['operation']
            if op=='health':
                from desk.ai_monitor import monitor_summary
                from django.core.serializers.json import DjangoJSONEncoder
                self.stdout.write(json.dumps(monitor_summary(), ensure_ascii=False, cls=DjangoJSONEncoder))
                return
            if op=='list':result=candidate_page(options['after_id'],options['limit'])
            else:
                if not enabled():raise CommandError('AI 工单自动跟进尚未启用。')
                if op in ['get','claim','retry','continue']:
                    if options['ticket_id']<1:raise ValueError()
                    if op=='get':result=ticket_context(Ticket.objects.get(pk=options['ticket_id']))
                    else:
                        if options['version']<1:raise ValueError()
                        if op=='continue':
                            raw=sys.stdin.read(65537)
                            if len(raw)>65536:raise ValueError()
                            result=request_blocked_followup(options['ticket_id'],options['version'],options['request_key'],json.loads(raw))
                        else:result=(request_retry if op=='retry' else claim)(options['ticket_id'],options['version'])
                else:
                    raw=sys.stdin.read(65537)
                    if len(raw)>65536:raise ValueError()
                    data=json.loads(raw)
                    expected={'lease_token','payload'} if op=='reply' else {'lease_token','reason'} if op=='defer' else {'lease_token'}
                    if not isinstance(data,dict) or set(data)!=expected:raise ValueError()
                    token=uuid.UUID(data['lease_token'])
                    result=publish(options['run_id'],token,data['payload']) if op=='reply' else defer(options['run_id'],token,data['reason']) if op=='defer' else renew(options['run_id'],token)
            self.stdout.write(json.dumps(result,ensure_ascii=False))
        except (Ticket.DoesNotExist,TicketAIRun.DoesNotExist):raise CommandError('工单或任务不存在。')
        except (ValueError,TypeError,ValidationError):raise CommandError('请求无效，请核对工单版本、任务租约及证据。')
