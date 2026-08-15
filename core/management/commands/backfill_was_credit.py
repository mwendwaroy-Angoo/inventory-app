from django.core.management.base import BaseCommand

from core.models import Transaction


class Command(BaseCommand):
    help = (
        "One-time repair for historical data affected by the 2026-08-15 "
        "'Paid exceeds Credit' bug (see Transaction.was_credit's own docstring "
        "for the full mechanism). was_credit is now stamped automatically going "
        "forward for any NEW transition off 'credit' — this command recovers it "
        "for transactions that were ALREADY resolved BEFORE this fix existed.\n\n"
        "Best-effort, NOT perfect: for a tab-linked transaction, BarTab.customer "
        "being set is a reliable, permanent signal that the tab was genuinely "
        "debt-converted at some point (KegBarrel.record_sale etc. always create "
        "a tab item as payment_method='credit', so ANY transaction linked to a "
        "debt-converted tab was, by construction, once genuinely debt-tracked — "
        "this recovers was_credit=True for ALL of them, regardless of current "
        "payment_method). For a DIRECT credit sale with NO tab at all (e.g. Quick "
        "Sell 'Deni', no BarTabEntry) that was ALREADY resolved before this fix "
        "shipped, there is no equivalent permanent signal left to recover from — "
        "those cannot be reliably identified by this command. If a specific "
        "customer's numbers still look wrong after running this, use "
        "diagnose_customer_debt --business=X --customer=Y to inspect their raw "
        "transaction history directly."
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Preview without saving.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        qs = Transaction.objects.filter(
            was_credit=False,
            tab_entry__tab__customer__isnull=False,
        ).select_related('tab_entry__tab', 'tab_entry__tab__customer', 'item')

        count = 0
        for txn in qs:
            tab = txn.tab_entry.tab
            self.stdout.write(
                f"  [{tab.business.name if hasattr(tab, 'business') else '?'}] "
                f"txn#{txn.id} tab#{tab.id} customer={tab.customer.name!r} "
                f"item={txn.item.description if txn.item_id else '?'} "
                f"KES {txn.revenue()} payment_method={txn.payment_method!r} -> was_credit=True"
            )
            if not dry_run:
                txn.was_credit = True
                # Bypass the save() override's dirty-check (payment_method isn't
                # changing here, only the historical marker) — direct .update()
                # equivalent via a plain field save is fine since this only ever
                # sets was_credit, never payment_method, in this command.
                txn.save(update_fields=['was_credit'])
            count += 1

        if dry_run:
            self.stdout.write(self.style.WARNING(f"DRY RUN — would backfill was_credit on {count} transaction(s)."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Backfilled was_credit on {count} transaction(s)."))
