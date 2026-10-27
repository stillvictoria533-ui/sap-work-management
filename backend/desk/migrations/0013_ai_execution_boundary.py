import django.db.models.deletion
import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('desk', '0012_expedite_and_schedule_batch')]

    operations = [
        migrations.AddField(model_name='ticketairun', name='accepted_scope', field=models.JSONField(default=dict)),
        migrations.CreateModel(name='TicketAIQuery', fields=[
            ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('request_key', models.UUIDField()),
            ('context_version', models.PositiveIntegerField()),
            ('lease_token', models.UUIDField()),
            ('plan', models.JSONField()),
            ('plan_hash', models.CharField(max_length=64)),
            ('status', models.CharField(default='reserved', max_length=12)),
            ('deadline_at', models.DateTimeField()),
            ('result_hash', models.CharField(blank=True, max_length=64)),
            ('error_code', models.CharField(blank=True, max_length=40)),
            ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
            ('finished_at', models.DateTimeField(null=True)),
            ('run', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='queries', to='desk.ticketairun')),
        ], options={'constraints': [models.UniqueConstraint(fields=('run', 'request_key'), name='unique_ai_query_request')]}),
    ]
