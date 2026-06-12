# Bar & Club Module — Sprint 1
# Adds: Item.is_keg, Item.volume_ml, Transaction.keg_barrel
# Creates: Shift, KegBarrel, KegWeightReading, BarTab, BarTabEntry

from decimal import Decimal

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0024_business_keg_settings'),
        ('core', '0042_alter_item_revenue_multiplier_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [

        # ── Item: keg flag + bottle volume ──────────────────────────────────
        migrations.AddField(
            model_name='item',
            name='is_keg',
            field=models.BooleanField(
                default=False,
                help_text='Keg item sold from a barrel by weight/volume. Stock tracked via KegBarrel envelopes, not normal balance.',
            ),
        ),
        migrations.AddField(
            model_name='item',
            name='volume_ml',
            field=models.PositiveIntegerField(
                null=True, blank=True,
                help_text='Bottle volume for single-piece liquor (750=mzinga, 350/375=half, 250=quarter).',
            ),
        ),

        # ── Shift ────────────────────────────────────────────────────────────
        migrations.CreateModel(
            name='Shift',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(
                    max_length=10, default='OPEN',
                    choices=[('OPEN', 'Open'), ('CLOSED', 'Closed — awaiting confirmation'),
                             ('CONFIRMED', 'Confirmed by incoming staff')],
                )),
                ('started_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('ended_at', models.DateTimeField(null=True, blank=True)),
                ('opening_float', models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))),
                ('closing_cash_counted', models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)),
                ('notes', models.TextField(blank=True)),
                ('business', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='shifts', to='accounts.business',
                )),
                ('store', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    to='core.store',
                )),
                ('staff', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='shifts', to=settings.AUTH_USER_MODEL,
                )),
                ('confirmed_by', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='shifts_confirmed', to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Shift',
                'verbose_name_plural': 'Shifts',
                'ordering': ['-started_at'],
            },
        ),

        # ── KegBarrel ────────────────────────────────────────────────────────
        migrations.CreateModel(
            name='KegBarrel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('gross_weight_kg', models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('60.00'))),
                ('tare_weight_kg',  models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('10.00'))),
                ('cost_price',     models.DecimalField(max_digits=10, decimal_places=2)),
                ('target_revenue', models.DecimalField(max_digits=10, decimal_places=2)),
                ('revenue_collected',   models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))),
                ('volume_dispensed_ml', models.DecimalField(
                    max_digits=10, decimal_places=2, default=Decimal('0'),
                    help_text='Sum of preset volumes sold — the BOOK figure. Compare with weight.',
                )),
                ('status', models.CharField(
                    max_length=10, default='SEALED',
                    choices=[('SEALED', 'Sealed — received, not tapped'), ('TAPPED', 'Tapped — selling'),
                             ('DEPLETED', 'Depleted — target reached / empty'), ('RETURNED', 'Returned / discarded')],
                )),
                ('received_on', models.DateField(default=django.utils.timezone.localdate)),
                ('tapped_at',  models.DateTimeField(null=True, blank=True)),
                ('closed_at',  models.DateTimeField(null=True, blank=True)),
                ('note',       models.CharField(max_length=120, blank=True)),
                ('business', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='keg_barrels', to='accounts.business',
                )),
                ('store', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    to='core.store',
                )),
                ('item', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='keg_barrels', to='core.item',
                )),
                ('received_by', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='kegs_received', to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Keg Barrel',
                'verbose_name_plural': 'Keg Barrels',
                'ordering': ['-received_on', '-id'],
            },
        ),

        # ── Transaction: keg_barrel discriminator FK ──────────────────────────
        migrations.AddField(
            model_name='transaction',
            name='keg_barrel',
            field=models.ForeignKey(
                null=True, blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='transactions', to='core.kegbarrel',
                help_text='The keg barrel this pour was drawn from. Discriminator for keg analytics — parallel to produce_bunch_id.',
            ),
        ),

        # ── KegWeightReading ─────────────────────────────────────────────────
        migrations.CreateModel(
            name='KegWeightReading',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('weight_kg',    models.DecimalField(max_digits=6, decimal_places=2)),
                ('reading_type', models.CharField(
                    max_length=12,
                    choices=[('RECEIVE', 'Received — verify 60 kg'), ('SHIFT_OPEN', 'Shift opening check'),
                             ('SHIFT_CLOSE', 'Shift closing check'), ('SPOT', 'Spot check'),
                             ('FINAL', 'Final / barrel empty')],
                )),
                ('recorded_at', models.DateTimeField(auto_now_add=True)),
                ('note',        models.CharField(max_length=120, blank=True)),
                ('barrel', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='weight_readings', to='core.kegbarrel',
                )),
                ('shift', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='keg_readings', to='core.shift',
                )),
                ('recorded_by', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='keg_readings_recorded', to=settings.AUTH_USER_MODEL,
                )),
                ('confirmed_by', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='keg_readings_confirmed', to=settings.AUTH_USER_MODEL,
                    help_text='Incoming staff who verified this reading at handover.',
                )),
            ],
            options={
                'verbose_name': 'Keg Weight Reading',
                'verbose_name_plural': 'Keg Weight Readings',
                'ordering': ['-recorded_at'],
            },
        ),

        # ── BarTab ────────────────────────────────────────────────────────────
        migrations.CreateModel(
            name='BarTab',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('customer_name', models.CharField(max_length=80)),
                ('server_name',   models.CharField(
                    max_length=80, blank=True,
                    help_text='Waitress name when she has no login.',
                )),
                ('status',     models.CharField(
                    max_length=8, default='OPEN',
                    choices=[('OPEN', 'Open'), ('SETTLED', 'Settled'), ('VOID', 'Void')],
                )),
                ('opened_at',  models.DateTimeField(auto_now_add=True)),
                ('settled_at', models.DateTimeField(null=True, blank=True)),
                ('business', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='bar_tabs', to='accounts.business',
                )),
                ('store', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    to='core.store',
                )),
                ('shift', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='tabs', to='core.shift',
                )),
                ('customer', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to='core.customer',
                    help_text='Optional link to a registered customer — enables debt tracker integration.',
                )),
                ('served_by', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='tabs_served', to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Bar Tab',
                'verbose_name_plural': 'Bar Tabs',
                'ordering': ['-opened_at'],
            },
        ),

        # ── BarTabEntry ───────────────────────────────────────────────────────
        migrations.CreateModel(
            name='BarTabEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('description',    models.CharField(max_length=80)),
                ('amount',         models.DecimalField(max_digits=10, decimal_places=2)),
                ('is_paid',        models.BooleanField(default=False)),
                ('paid_at',        models.DateTimeField(null=True, blank=True)),
                ('payment_method', models.CharField(max_length=10, blank=True)),
                ('tab', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='entries', to='core.bartab',
                )),
                ('transaction', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='tab_entry', to='core.transaction',
                )),
            ],
            options={
                'verbose_name': 'Bar Tab Entry',
                'verbose_name_plural': 'Bar Tab Entries',
                'ordering': ['id'],
            },
        ),
    ]
