"""
UBA §7.4 (Sprint R3) — bulk ABC classification across every business. Not
wired to an automatic schedule this pass (documented deferral, same
convention as R1's recompute_market_price_index and M3's daily digest) —
run manually or from a future Render cron job.
"""
from accounts.models import Business
from core.cycle_count import classify_abc_all
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Classify every business\'s items into ABC classes by 90-day revenue.'

    def handle(self, *args, **options):
        total = 0
        for business in Business.objects.all():
            total += classify_abc_all(business)
        self.stdout.write(self.style.SUCCESS(f'Reclassified {total} item(s) across all businesses.'))
