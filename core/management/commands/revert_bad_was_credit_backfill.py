from django.core.management.base import BaseCommand

from core.models import Transaction


class Command(BaseCommand):
    help = (
        "EMERGENCY UNDO for backfill_was_credit (2026-08-15) — that command used "
        "`tab.customer_id is not null` as its signal for 'this was genuinely debt "
        "at some point'. That signal is WRONG: an ORDINARY tab, paid in full the "
        "same visit, ALSO gets a Customer record auto-attached to it the moment it "
        "fully settles (see BarTab settle code — 'auto-create Customer record on "
        "any settlement, not just credit') — completely unrelated to Geuza Deni / "
        "real debt. Running that backfill resurrected the ENTIRE historical tab "
        "total for every customer who ever had a tab settle, not just genuine "
        "debt — the exact live report this command undoes.\n\n"
        "Clears was_credit back to False for every transaction matching that same "
        "over-broad signal. Note: this is a blunt instrument — it may also clear "
        "a small number of transactions that were CORRECTLY stamped by the real, "
        "narrower fix (Transaction.save()'s own tab-status-at-transition check) "
        "in the short window between deploy and running this. That's an accepted "
        "trade-off — a handful of recent, genuinely-still-owed debts needing a "
        "second look is far safer than every customer's paid-off history showing "
        "as currently owed. backfill_was_credit itself has been retired — do not "
        "run it again; there is no reliable way to infer this after the fact from "
        "current-state data alone, only at the moment of a real transition."
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Preview without saving.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        qs = Transaction.objects.filter(
            was_credit=True,
            tab_entry__tab__customer__isnull=False,
        ).select_related('tab_entry__tab', 'tab_entry__tab__customer', 'item')

        count = 0
        for txn in qs:
            tab = txn.tab_entry.tab
            self.stdout.write(
                f"  txn#{txn.id} tab#{tab.id} customer={tab.customer.name!r} "
                f"item={txn.item.description if txn.item_id else '?'} "
                f"KES {txn.revenue()} payment_method={txn.payment_method!r} -> was_credit=False"
            )
            if not dry_run:
                txn.was_credit = False
                txn.save(update_fields=['was_credit'])
            count += 1

        if dry_run:
            self.stdout.write(self.style.WARNING(f"DRY RUN — would revert was_credit on {count} transaction(s)."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Reverted was_credit on {count} transaction(s)."))
