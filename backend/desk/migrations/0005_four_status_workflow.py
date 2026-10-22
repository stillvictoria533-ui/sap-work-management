from django.db import migrations, models
from django.utils import timezone


def merge_closed(apps, schema_editor):
    Ticket = apps.get_model('desk', 'Ticket')
    Event = apps.get_model('desk', 'Event')
    db = schema_editor.connection.alias
    now = timezone.now()
    for ticket in Ticket.objects.using(db).filter(status='closed').iterator():
        ticket.status = 'done'
        ticket.next_actor = 'none'
        ticket.version += 1
        ticket.updated_at = now
        ticket.save(using=db, update_fields=['status', 'next_actor', 'version', 'updated_at'])
        Event.objects.using(db).create(
            ticket_id=ticket.pk, actor_id=ticket.owner_id, actor_name='系统', actor_role='system',
            kind='status', from_status='closed', to_status='done', version=ticket.version,
            body='状态体系升级：历史已关闭工单统一归为已完成，原沟通记录保留。', created_at=now,
        )
    Ticket.objects.using(db).filter(status='waiting').update(next_actor='consultant')
    Ticket.objects.using(db).filter(status='done').update(next_actor='none')


class Migration(migrations.Migration):
    dependencies = [('desk', '0004_user_dingtalk_mobile_alter_delivery_channel_and_more')]
    operations = [
        migrations.RunPython(merge_closed),
        migrations.AlterField(model_name='ticket', name='status', field=models.CharField(
            max_length=20, default='pending', choices=[('pending', '待受理'), ('in-progress', '处理中'), ('waiting', '待验收'), ('done', '已完成')],
        )),
    ]
