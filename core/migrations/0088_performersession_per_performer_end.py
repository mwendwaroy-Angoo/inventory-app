from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0087_performersession_expected_hours'),
    ]

    operations = [
        migrations.AddField(
            model_name='performersession',
            name='performer_ended_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='performersession',
            name='second_performer_ended_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
