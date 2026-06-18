from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0030_userprofile_session_control'),
    ]

    operations = [
        migrations.AddField(
            model_name='business',
            name='daraja_environment',
            field=models.CharField(
                max_length=20,
                choices=[('sandbox', 'Sandbox'), ('production', 'Production')],
                default='sandbox',
                help_text='Select Production only after Safaricom has approved your go-live request for this shortcode.',
            ),
        ),
    ]
