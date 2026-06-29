from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0080_performer_session'),
    ]

    operations = [
        migrations.AlterField(
            model_name='performerfeedback',
            name='comment',
            field=models.TextField(blank=True, max_length=500),
        ),
    ]
