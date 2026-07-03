"""
Sprint DJ3: Duo support + two/three-step confirmation + payment privacy.

Two-step approach for second_performer_checkin_token (unique UUID on existing rows):
  1. Add column as nullable.
  2. RunPython to populate unique UUIDs for all existing rows.
  3. AlterField to NOT NULL + unique.
"""
import uuid as uuid_mod

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def _populate_second_checkin_tokens(apps, schema_editor):
    PerformerSession = apps.get_model('core', 'PerformerSession')
    for session in PerformerSession.objects.filter(second_performer_checkin_token__isnull=True):
        session.second_performer_checkin_token = uuid_mod.uuid4()
        session.save(update_fields=['second_performer_checkin_token'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0084_remove_performerfeedback_ip_hash'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── Duo: second performer FK ──────────────────────────────────────────
        migrations.AddField(
            model_name='performersession',
            name='second_performer',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='second_performer_sessions',
                to='core.performer',
            ),
        ),
        # ── Duo: second performer confirmation fields ─────────────────────────
        migrations.AddField(
            model_name='performersession',
            name='second_performer_checked_in',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='performersession',
            name='second_performer_checkin_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        # Step 1: add as nullable first so existing rows get no constraint yet
        migrations.AddField(
            model_name='performersession',
            name='second_performer_checkin_token',
            field=models.UUIDField(blank=True, null=True),
        ),
        # Step 2: populate unique UUIDs for every existing row
        migrations.RunPython(_populate_second_checkin_tokens, migrations.RunPython.noop),
        # Step 3: make NOT NULL + unique
        migrations.AlterField(
            model_name='performersession',
            name='second_performer_checkin_token',
            field=models.UUIDField(default=uuid_mod.uuid4, editable=False, unique=True),
        ),
        # ── Staff on-ground confirmation ──────────────────────────────────────
        migrations.AddField(
            model_name='performersession',
            name='staff_confirmed',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='performersession',
            name='staff_confirmed_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='dj_confirmations',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='performersession',
            name='staff_confirmed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        # ── Add PENDING_CONFIRMATION status + widen max_length ────────────────
        migrations.AlterField(
            model_name='performersession',
            name='status',
            field=models.CharField(
                choices=[
                    ('SCHEDULED',            'Scheduled / upcoming'),
                    ('PENDING_APPROVAL',      'Pending owner approval'),
                    ('PENDING_CONFIRMATION',  'Awaiting confirmation'),
                    ('ACTIVE',               'Active / in progress'),
                    ('COMPLETED',            'Completed'),
                    ('CANCELLED',            'Cancelled / no-show'),
                ],
                default='PENDING_CONFIRMATION',
                max_length=22,
            ),
        ),
    ]
