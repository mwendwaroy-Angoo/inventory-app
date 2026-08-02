"""
UBA §6.2 (Sprint P0-B) — bulk layaway hold-expiry reminder run across every
business. Not wired to an automatic schedule this pass (documented
deferral, same convention as R1/R3/X1/M3) — run manually or from a future
Render cron job.
"""
from accounts.models import Business
from core.payment_plans_views import send_hold_expiry_reminders
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Send owner reminders for layaway holds expiring soon, across all businesses.'

    def handle(self, *args, **options):
        total = 0
        for business in Business.objects.all():
            total += send_hold_expiry_reminders(business)
        self.stdout.write(self.style.SUCCESS(f'Reminded on {total} plan(s) across all businesses.'))
