from django.core.management.base import BaseCommand, CommandError

from core.models import CustomerDebtPayment


class Command(BaseCommand):
    help = (
        "2026-08-06 live fix companion (Monsoon Inn) — the customer_debt_profile.html "
        "ledger-selector radio used to hard-code 'Bar' pre-checked, so an owner/manager "
        "payment meant for a customer's KITCHEN debt could silently be recorded tagged "
        "'bar' if nobody switched it (fixed going forward — see debt_views.py). This "
        "command safely re-tags a SPECIFIC, already-identified payment (use "
        "audit_debt_payment_ledger_split first to find candidates) — it only changes "
        "CustomerDebtPayment.source, never the payment amount, and prints the affected "
        "customer's bar/kitchen outstanding before and after so the correction can be "
        "verified on the spot."
    )

    def add_arguments(self, parser):
        parser.add_argument('payment_id', type=int)
        parser.add_argument('new_source', choices=['bar', 'kitchen'])
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would change without saving anything.',
        )

    def handle(self, *args, **options):
        from core.debt_views import _get_customer_debt_data

        try:
            pay = CustomerDebtPayment.objects.select_related('customer', 'business').get(
                id=options['payment_id']
            )
        except CustomerDebtPayment.DoesNotExist:
            raise CommandError(f"No CustomerDebtPayment with id={options['payment_id']}")

        if pay.source == options['new_source']:
            self.stdout.write(self.style.WARNING(
                f"Payment #{pay.id} is already tagged '{pay.source}' — nothing to do."
            ))
            return

        business = pay.business
        customer = pay.customer
        before_bar = _get_customer_debt_data(customer, business, scope='bar')['outstanding']
        before_kitchen = _get_customer_debt_data(customer, business, scope='kitchen')['outstanding']

        self.stdout.write(
            f"Payment #{pay.id}: KES {pay.amount_paid:,.2f} for {customer.name} "
            f"({business.name}) — '{pay.source}' → '{options['new_source']}'"
        )
        self.stdout.write(
            f"  Before: 🍺 Bar owing KES {before_bar:,.2f} · 🍗 Kitchen owing KES {before_kitchen:,.2f}"
        )

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes saved.'))
            return

        pay.source = options['new_source']
        pay.save(update_fields=['source'])

        after_bar = _get_customer_debt_data(customer, business, scope='bar')['outstanding']
        after_kitchen = _get_customer_debt_data(customer, business, scope='kitchen')['outstanding']
        self.stdout.write(
            f"  After:  🍺 Bar owing KES {after_bar:,.2f} · 🍗 Kitchen owing KES {after_kitchen:,.2f}"
        )
        self.stdout.write(self.style.SUCCESS(f"Payment #{pay.id} reassigned to '{options['new_source']}'."))
