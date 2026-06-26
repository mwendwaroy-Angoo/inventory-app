from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0041_business_credit_policy'),
    ]

    operations = [
        migrations.AddField(
            model_name='business',
            name='weighs_kegs',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'Bar has a scale. Enables weight-based auto-depletion and light-at-tap theft detection. '
                    'Without weighing the app tracks recorded sales + envelope only and cannot detect fully off-book theft.'
                ),
            ),
        ),
        migrations.AddField(
            model_name='business',
            name='block_sales_past_target',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'Block all sales once a barrel hits its revenue target. '
                    'Default off — staff are prompted to close or continue knowingly instead.'
                ),
            ),
        ),
    ]
