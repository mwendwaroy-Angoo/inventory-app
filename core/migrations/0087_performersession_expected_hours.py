from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0086_performer_photo_and_second_fee'),
    ]

    operations = [
        migrations.AddField(
            model_name='performersession',
            name='expected_hours',
            field=models.DecimalField(
                blank=True,
                decimal_places=1,
                help_text='Agreed session duration in hours — shown as accountability timer on home dashboard',
                max_digits=4,
                null=True,
            ),
        ),
    ]
