from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0029_business_daraja_passkey'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='current_session_key',
            field=models.CharField(
                blank=True,
                max_length=40,
                help_text='Session key of the most recent login. Used to enforce single active session per user.',
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='allow_concurrent_sessions',
            field=models.BooleanField(
                default=False,
                help_text='If True, this user may be logged in from multiple devices at once (e.g. for dev/testing).',
            ),
        ),
    ]
