from django.core.management.base import BaseCommand

from core.models import BarTabEntry


class Command(BaseCommand):
    help = (
        "General-purpose repair for one recurring mismatch signature — a "
        "BarTabEntry marked paid via cash/mpesa whose own Transaction is "
        "still stuck on payment_method='credit' — caused by TWO SEPARATE "
        "bugs found on two different dates, both now fixed, both producing "
        "the identical broken row shape:\n"
        "  (1) 2026-07-31 — BarTabEntry.split_paid_unpaid_locked() (the "
        "      partial-amount tab-settle split) marked the KEPT/PAID "
        "      portion's entry correctly but never synced its Transaction "
        "      (live report: Hezzy's tab showed BOTH the paid and unpaid "
        "      portions as still owed once converted to debt).\n"
        "  (2) 2026-08-14 — debt_views._do_settle_debt_payment()'s FIFO "
        "      BarTabEntry reconciliation (fires when a debt payment covers "
        "      an entry on an already-converted-to-debt tab) used a bulk "
        "      .update() on BarTabEntry only, never touching the "
        "      Transaction — confirmed live via audit_debt_ledger_integrity "
        "      --all-customers: dozens of real transactions across many "
        "      customers, permanently mis-tagged as outstanding credit.\n"
        "In both cases the debt tracker's credit_qs and shift_views."
        "_reconcile()'s cash/mpesa/credit totals read Transaction."
        "payment_method directly, independent of BarTabEntry.is_paid — so "
        "every affected row stays permanently mis-tagged as outstanding "
        "debt and permanently missing from real cash/mpesa collected until "
        "repaired. Finds every BarTabEntry that IS marked paid via cash/"
        "mpesa but whose Transaction is still stuck on 'credit', and syncs "
        "the Transaction to match. Safe to re-run — only touches rows "
        "still in the broken state, regardless of which bug produced them."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='List what would be fixed without saving anything.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        qs = (
            BarTabEntry.objects.filter(
                is_paid=True,
                payment_method__in=['cash', 'mpesa'],
                transaction__payment_method='credit',
            )
            .select_related('transaction', 'tab__business')
        )

        count = 0
        for entry in qs:
            txn = entry.transaction
            self.stdout.write(
                f"  [{entry.tab.business.name}] tab #{entry.tab_id} — {entry.description} "
                f"KES {entry.amount} — txn #{txn.id}: credit -> {entry.payment_method}"
            )
            if not dry_run:
                txn.payment_method = entry.payment_method
                txn.save(update_fields=['payment_method'])
            count += 1

        if dry_run:
            self.stdout.write(self.style.WARNING(f"DRY RUN — would fix {count} transaction(s)."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Fixed {count} transaction(s)."))
