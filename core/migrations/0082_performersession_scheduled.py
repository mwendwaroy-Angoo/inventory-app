from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0081_performerfeedback_comment_maxlength'),
    ]

    operations = [
        migrations.AddField(
            model_name='performersession',
            name='scheduled_start_time',
            field=models.TimeField(blank=True, null=True),
        ),
    ]
