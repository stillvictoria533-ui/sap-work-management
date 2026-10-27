from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('desk', '0013_ai_execution_boundary')]
    operations = [migrations.AddField(
        model_name='ticket', name='submission_fingerprint',
        field=models.CharField(blank=True, default='', max_length=64),
    )]
