from django.db import migrations, models
import django.utils.timezone


def seed_singletons(apps, schema_editor):
    alias = schema_editor.connection.alias
    apps.get_model('desk', 'UploadQuotaLock').objects.using(alias).get_or_create(pk=1)
    apps.get_model('desk', 'TicketAIExecutorState').objects.using(alias).get_or_create(pk='primary')


class Migration(migrations.Migration):
    dependencies = [('desk', '0014_ticket_submission_fingerprint')]
    operations = [
        migrations.CreateModel(
            name='TicketAIExecutorState',
            fields=[
                ('name', models.CharField(default='primary', editable=False, max_length=16, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('last_seen_at', models.DateTimeField(blank=True, null=True)),
                ('last_scan_at', models.DateTimeField(blank=True, null=True)),
                ('capabilities', models.JSONField(default=dict)),
            ],
        ),
        migrations.CreateModel(
            name='UploadQuotaLock',
            fields=[('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False))],
            options={'constraints': [models.CheckConstraint(condition=models.Q(id=1), name='one_upload_quota_lock')]},
        ),
        migrations.RunPython(seed_singletons, migrations.RunPython.noop),
    ]
