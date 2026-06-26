from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0070_salary_payment_model'),
    ]

    operations = [
        migrations.AddField(
            model_name='customer',
            name='is_defaulter',
            field=models.BooleanField(
                default=False,
                help_text='Had a debt written off as bad debt; permanently high-risk flag.',
            ),
        ),
        migrations.AddField(
            model_name='customer',
            name='last_cleared_at',
            field=models.DateTimeField(
                null=True, blank=True,
                help_text='Timestamp when this customer last had their outstanding balance reach zero.',
            ),
        ),
    ]
