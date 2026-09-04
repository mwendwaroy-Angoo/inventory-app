"""
UBA §10.3 (Sprint L1) — bulk rent-roll generation across every business,
for the CURRENT calendar month. Not wired to an automatic schedule this
pass (documented deferral, same convention as every other deferred-cron
feature this session) — run manually or from a future Render cron job on
the 1st of each month. Idempotent — see core.rentals.generate_rent_roll's
own docstring.
"""
import calendar

from accounts.models import Business
from core.rentals import generate_rent_roll
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Generate this month's rent roll for every business's active rental agreements."

    def handle(self, *args, **options):
        today = timezone.localdate()
        period_start = today.replace(day=1)
        period_end = today.replace(day=calendar.monthrange(today.year, today.month)[1])
        due_date = today.replace(day=5) if today.day <= 5 else period_end

        total = 0
        for business in Business.objects.all():
            created = generate_rent_roll(business, period_start, period_end, due_date)
            total += len(created)
        self.stdout.write(self.style.SUCCESS(f'Generated {total} rent invoice(s) across all businesses.'))
