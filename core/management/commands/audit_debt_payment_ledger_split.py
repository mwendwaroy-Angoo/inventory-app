from django.core.management.base import BaseCommand

from accounts.models import Business
from core.models import Customer, CustomerDebtPayment


class Command(BaseCommand):
    help = (
        "Read-only diagnostic (2026-08-06 live report, Monsoon Inn) — lists every "
        "debt payment recorded by an OWNER or MANAGER (the only case the debt_source "
        "ledger-picker radio could ever have been mistagged, since a station-scoped "
        "staffer's payment_scope is auto-derived and never ambiguous) for a business "
        "with a kitchen module, alongside that customer's bar/kitchen outstanding "
        "split at the time this command runs. This does NOT change any data — it only "
        "surfaces candidates for a human (who knows what actually happened) to review "
        "and, if needed, correct with reassign_debt_payment_source. Run once against "
        "production via Render Shell to sanity-check historical payments before/after "
        "the 2026-08-06 fix to customer_debt_profile.html's ledger-selector default."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--business', type=str, default='',
            help='Business name to check (case-insensitive substring match). '
                 'Omit to check every business with has_kitchen=True.',
        )

    def handle(self, *args, **options):
        from core.debt_views import _get_customer_debt_data

        biz_filter = options['business'].strip()
        businesses = Business.objects.filter(has_kitchen=True)
        if biz_filter:
            businesses = businesses.filter(name__icontains=biz_filter)

        if not businesses.exists():
            self.stdout.write(self.style.WARNING('No matching kitchen-enabled business found.'))
            return

        total_flagged = 0
        for business in businesses:
            payments = CustomerDebtPayment.objects.filter(
                business=business,
                recorded_by__userprofile__role__in=['owner', 'manager'],
            ).select_related('customer', 'recorded_by').order_by('customer_id', 'paid_at')

            if not payments.exists():
                continue

            self.stdout.write(self.style.MIGRATE_HEADING(f'\n=== {business.name} ==='))
            seen_customers = set()
            for pay in payments:
                cust = pay.customer
                if cust.id not in seen_customers:
                    seen_customers.add(cust.id)
                    bar_data = _get_customer_debt_data(cust, business, scope='bar')
                    kitchen_data = _get_customer_debt_data(cust, business, scope='kitchen')
                    self.stdout.write(
                        f"\n  Customer: {cust.name} — 🍺 Bar owing KES {bar_data['outstanding']:,.2f} · "
                        f"🍗 Kitchen owing KES {kitchen_data['outstanding']:,.2f}"
                    )
                recorder = pay.recorded_by.get_full_name() or pay.recorded_by.username if pay.recorded_by else '?'
                self.stdout.write(
                    f"    · #{pay.id}: KES {pay.amount_paid:,.2f} tagged '{pay.source}' "
                    f"by {recorder} on {pay.paid_at.strftime('%Y-%m-%d %H:%M')} — {pay.notes or 'no note'}"
                )
                total_flagged += 1

        if total_flagged:
            self.stdout.write(self.style.WARNING(
                f'\n{total_flagged} owner/manager-recorded payment(s) listed above for review. '
                "If any customer's kitchen debt looks like it should have been reduced by a "
                "payment currently tagged 'bar' (or vice versa), use:\n"
                "  python manage.py reassign_debt_payment_source <payment_id> <bar|kitchen>"
            ))
        else:
            self.stdout.write(self.style.SUCCESS('No owner/manager-recorded debt payments found to review.'))
