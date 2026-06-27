from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.db.models.fields
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0074_barcuplog_business_level'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── Item.is_kitchen_batch ─────────────────────────────────────────────
        migrations.AddField(
            model_name='item',
            name='is_kitchen_batch',
            field=models.BooleanField(
                default=False,
                help_text='Kitchen batch item — sold by price point from an open KitchenBatch. '
                          'Used for chips, stew, ugali and other cooked-to-batch food. '
                          'Stock is NOT counted by unit; the batch tracks cost vs revenue.',
            ),
        ),

        # ── ItemPortionPreset.khaki_type ──────────────────────────────────────
        migrations.AddField(
            model_name='itemportionpreset',
            name='khaki_type',
            field=models.CharField(
                max_length=8,
                choices=[
                    ('NONE',  'No khaki bag used'),
                    ('SMALL', '1/4 Khaki (small)'),
                    ('LARGE', '1/2 Khaki (large)'),
                ],
                default='NONE',
                help_text='For kitchen batch presets: how many khaki bags this serving uses. '
                          'Drives the business-wide khaki pool deduction counter.',
            ),
        ),

        # ── KitchenBatch model ────────────────────────────────────────────────
        migrations.CreateModel(
            name='KitchenBatch',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cost_total', models.DecimalField(
                    decimal_places=2, default=0,
                    help_text='Total raw-material cost for this batch (e.g. cost of potatoes, nyama etc.).',
                    max_digits=10,
                )),
                ('cost_note', models.CharField(
                    blank=True, max_length=200,
                    help_text='Optional note: "2 debe ya viazi @ 750 = 1500".',
                )),
                ('revenue_collected', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('khaki_small_used', models.PositiveIntegerField(
                    default=0,
                    help_text='1/4 khaki bags consumed from this batch (deducted from business khaki pool).',
                )),
                ('khaki_large_used', models.PositiveIntegerField(
                    default=0,
                    help_text='1/2 khaki bags consumed from this batch.',
                )),
                ('status', models.CharField(
                    choices=[
                        ('OPEN', 'Open — selling'),
                        ('DEPLETED', 'Depleted — all sold'),
                        ('DISCARDED', 'Discarded — went to waste'),
                    ],
                    default='OPEN', max_length=12,
                )),
                ('received_on', models.DateField(default=django.utils.timezone.localdate)),
                ('closed_on', models.DateTimeField(blank=True, null=True)),
                ('note', models.CharField(blank=True, max_length=200)),
                ('business', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='kitchen_batches',
                    to='accounts.business',
                )),
                ('store', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='kitchen_batches',
                    to='core.store',
                )),
                ('item', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='kitchen_batches',
                    to='core.item',
                )),
                ('recorded_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='kitchen_batches_recorded',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Kitchen Batch',
                'verbose_name_plural': 'Kitchen Batches',
                'ordering': ['-received_on', '-id'],
            },
        ),

        # ── KitchenConsumableLog model ────────────────────────────────────────
        migrations.CreateModel(
            name='KitchenConsumableLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('consumable_type', models.CharField(
                    choices=[
                        ('KHAKI_SMALL', '1/4 Khaki bags'),
                        ('KHAKI_LARGE', '1/2 Khaki bags'),
                        ('SAUCE_TOMATO', 'Tomato sauce (jerrican)'),
                        ('OTHER', 'Other'),
                    ],
                    max_length=16,
                )),
                ('qty', models.DecimalField(
                    decimal_places=1, max_digits=8,
                    help_text='Units bought: pieces for khaki, jerricans for sauce.',
                )),
                ('unit_cost', models.DecimalField(decimal_places=2, max_digits=8)),
                ('total_cost', models.DecimalField(decimal_places=2, max_digits=10)),
                ('date', models.DateField(default=django.utils.timezone.localdate)),
                ('note', models.CharField(blank=True, max_length=120)),
                ('business', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='kitchen_consumable_logs',
                    to='accounts.business',
                )),
                ('recorded_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='kitchen_consumable_logs_recorded',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Kitchen Consumable Log',
                'verbose_name_plural': 'Kitchen Consumable Logs',
                'ordering': ['-date', '-id'],
            },
        ),

        # ── Transaction.kitchen_batch FK ──────────────────────────────────────
        migrations.AddField(
            model_name='transaction',
            name='kitchen_batch',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='sales',
                to='core.kitchenbatch',
                help_text='Kitchen batch this sale was drawn from. Discriminator for kitchen batch analytics.',
            ),
        ),
    ]
