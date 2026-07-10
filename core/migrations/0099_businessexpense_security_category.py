from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0098_bartab_add_qs_source'),
    ]

    operations = [
        migrations.AlterField(
            model_name='businessexpense',
            name='category',
            field=models.CharField(
                choices=[
                    ('labor', 'Labor / Salaries'),
                    ('electricity', 'Electricity Bills'),
                    ('rent', 'Rent'),
                    ('utilities', 'Utilities (Water, Internet)'),
                    ('transport', 'Transport / Logistics'),
                    ('marketing', 'Marketing & Advertising'),
                    ('maintenance', 'Maintenance & Repairs'),
                    ('supplies', 'Office Supplies'),
                    ('tax', 'Taxes & Licenses'),
                    ('entertainment', 'Entertainment / DJ / MC Fees'),
                    ('security', 'Security & Facilitation'),
                    ('other', 'Other'),
                ],
                default='other',
                max_length=20,
            ),
        ),
    ]
