from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0028_business_daraja_credentials'),
    ]

    operations = [
        migrations.AddField(
            model_name='business',
            name='daraja_passkey',
            field=models.CharField(
                blank=True,
                help_text='Safaricom Daraja Passkey for STK Push (issued at Daraja go-live)',
                max_length=200,
            ),
        ),
    ]
