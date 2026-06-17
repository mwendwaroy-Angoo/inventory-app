from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0058_payment_bar_tab'),
    ]

    operations = [
        migrations.AddField(
            model_name='pendingtransactionprompt',
            name='receipt',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='prompts',
                to='core.receipt',
            ),
        ),
    ]
