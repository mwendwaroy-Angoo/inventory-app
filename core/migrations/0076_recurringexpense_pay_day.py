from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0075_kitchen_batch'),
    ]

    operations = [
        migrations.AddField(
            model_name='recurringexpense',
            name='pay_day',
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text='Day of month salary is due (1–28). 0 = last day of the month.',
            ),
        ),
    ]
