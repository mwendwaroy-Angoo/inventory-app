from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0085_performer_session_duo_and_staff_confirm'),
    ]

    operations = [
        migrations.AddField(
            model_name='performer',
            name='photo_url',
            field=models.CharField(
                max_length=500, blank=True, default='',
                help_text='Public image URL — shown on promo page and roster',
            ),
        ),
        migrations.AddField(
            model_name='performersession',
            name='second_performer_fee',
            field=models.DecimalField(
                max_digits=10, decimal_places=2, default=0,
                help_text='Agreed fee for the second performer (duo sessions only)',
            ),
        ),
    ]
