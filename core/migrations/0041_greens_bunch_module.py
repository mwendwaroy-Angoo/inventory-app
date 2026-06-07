# Greens / bunch-based produce (Kibanda Produce Module)
from decimal import Decimal

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0023_add_last_txn_sms_at_to_business'),
        ('core', '0040_produce_module_portion_presets'),
    ]

    operations = [
        # ── Item: bunch-mode config ──────────────────────────────────────
        migrations.AddField(
            model_name='item',
            name='produce_mode',
            field=models.CharField(
                max_length=10, default='PORTION',
                choices=[('PORTION', 'Portion / fraction (cabbage, gorogoro)'),
                         ('BUNCH', 'Bunch — revenue envelope (greens / mboga)')],
                help_text='PORTION = a fixed quantity per price (cabbage = 0.25 head, '
                          'gorogoro = 1 tin). BUNCH = each bunch is a money target depleted '
                          'by price-point sales (sukuma, spinach, kienyeji).',
            ),
        ),
        migrations.AddField(
            model_name='item',
            name='mix_group',
            field=models.CharField(
                max_length=40, blank=True, default='',
                help_text='Tag greens that can be sold together as one generic order — '
                          'e.g. "kienyeji". Items sharing a tag appear under a single mix '
                          'tile and a generic "mboga za kienyeji ya 20" is split across them. '
                          'Leave blank for greens only ever sold by name (e.g. sukuma, spinach).',
            ),
        ),
        migrations.AddField(
            model_name='item',
            name='revenue_multiplier',
            field=models.DecimalField(
                max_digits=4, decimal_places=2, default=Decimal('1.70'),
                help_text='Default markup used to pre-fill a bunch target from its market '
                          'cost (1.70 -> a 40/= bunch targets 68/=). Overridable per bunch.',
            ),
        ),
        # ── Transaction: real cash on the line ───────────────────────────
        migrations.AddField(
            model_name='transaction',
            name='sale_amount',
            field=models.DecimalField(
                max_digits=10, decimal_places=2, null=True, blank=True,
                help_text='Actual cash taken for this sale line. Set for produce / bunch '
                          'portion sales where the price is NOT selling_price x qty. '
                          'Preferred by revenue().',
            ),
        ),
        # ── ProduceBunch ─────────────────────────────────────────────────
        migrations.CreateModel(
            name='ProduceBunch',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('size', models.CharField(max_length=10, default='MEDIUM',
                    choices=[('SMALL', 'Small'), ('MEDIUM', 'Medium'), ('LARGE', 'Large')])),
                ('cost_price', models.DecimalField(max_digits=10, decimal_places=2,
                    help_text='What this bunch cost at the market this morning.')),
                ('target_revenue', models.DecimalField(max_digits=10, decimal_places=2,
                    help_text='Total money this bunch must give before it is finished. '
                              'Pre-filled from cost x the item multiplier; override per bunch by eye.')),
                ('revenue_collected', models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))),
                ('status', models.CharField(max_length=10, default='OPEN',
                    choices=[('OPEN', 'Open'), ('DEPLETED', 'Depleted'), ('DISCARDED', 'Discarded / wilted')])),
                ('received_on', models.DateField(default=django.utils.timezone.localdate,
                    help_text='Market day this bunch was bought — drives sell-oldest-first and wilting alerts.')),
                ('opened_on', models.DateTimeField(null=True, blank=True)),
                ('closed_on', models.DateTimeField(null=True, blank=True)),
                ('note', models.CharField(max_length=200, blank=True, default='')),
                ('business', models.ForeignKey(null=True, blank=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='produce_bunches', to='accounts.business')),
                ('item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name='bunches', to='core.item')),
            ],
            options={
                'verbose_name': 'Produce Bunch',
                'verbose_name_plural': 'Produce Bunches',
                'ordering': ['received_on', 'id'],
            },
        ),
        # ── Transaction -> ProduceBunch link (after the model exists) ─────
        migrations.AddField(
            model_name='transaction',
            name='produce_bunch',
            field=models.ForeignKey(
                null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='sales', to='core.producebunch',
                help_text='The greens bunch this portion sale was drawn from, if any.',
            ),
        ),
    ]
