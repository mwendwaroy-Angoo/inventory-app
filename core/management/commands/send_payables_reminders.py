"""
UBA §12.1 (Sprint X1) — bulk payables due-reminder run across every
business. Not wired to an automatic schedule this pass (documented
deferral, same convention as R1/R3/M3) — run manually or from a future
Render cron job.
"""
from accounts.models import Business
from core.payables_views import send_payables_due_reminders
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send owner reminders for supplier invoices due soon or overdue, across all businesses."

    def handle(self, *args, **options):
        total = 0
        for business in Business.objects.all():
            total += send_payables_due_reminders(business)
        self.stdout.write(self.style.SUCCESS(f'Reminded on {total} invoice(s) across all businesses.'))
