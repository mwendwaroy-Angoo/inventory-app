from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0043_bar_module'),
    ]

    operations = [
        # keg_type on Item
        migrations.AddField(
            model_name='item',
            name='keg_type',
            field=models.CharField(
                blank=True,
                choices=[
                    ('REGULAR', 'Regular (Lager)'),
                    ('DARK', 'Dark / Stout'),
                    ('GOLD', 'Gold (Premium)'),
                ],
                help_text='Keg items only — beer type for analytics grouping (Regular, Dark, Gold).',
                max_length=8,
            ),
        ),
        # BarCupLog model
        migrations.CreateModel(
            name='BarCupLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cup_size', models.CharField(
                    choices=[('300', '300 ml'), ('500', '500 ml')],
                    default='300',
                    max_length=3,
                )),
                ('qty', models.PositiveIntegerField()),
                ('unit_cost', models.DecimalField(decimal_places=2, max_digits=8)),
                ('total_cost', models.DecimalField(decimal_places=2, max_digits=10)),
                ('date', models.DateField(default=django.utils.timezone.localdate)),
                ('note', models.CharField(blank=True, max_length=120)),
                ('barrel', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='cup_logs',
                    to='core.kegbarrel',
                )),
                ('business', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    to='accounts.business',
                )),
            ],
            options={
                'verbose_name': 'Bar Cup Log',
                'verbose_name_plural': 'Bar Cup Logs',
                'ordering': ['-date', '-id'],
            },
        ),
        # ProduceOverhead model
        migrations.CreateModel(
            name='ProduceOverhead',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('overhead_type', models.CharField(
                    choices=[
                        ('BAGS', 'Polythene Bags'),
                        ('WATER', 'Water (washing greens)'),
                        ('TRANSPORT', 'Transport'),
                        ('OTHER', 'Other'),
                    ],
                    default='OTHER',
                    max_length=12,
                )),
                ('qty', models.PositiveIntegerField(default=1)),
                ('cost', models.DecimalField(decimal_places=2, max_digits=8)),
                ('date', models.DateField(default=django.utils.timezone.localdate)),
                ('note', models.CharField(blank=True, max_length=120)),
                ('business', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='produce_overheads',
                    to='accounts.business',
                )),
                ('bunch', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='overheads',
                    to='core.producebunch',
                )),
            ],
            options={
                'verbose_name': 'Produce Overhead',
                'verbose_name_plural': 'Produce Overheads',
                'ordering': ['-date', '-id'],
            },
        ),
    ]
