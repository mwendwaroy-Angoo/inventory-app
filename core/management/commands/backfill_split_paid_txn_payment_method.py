from django.core.management.base import BaseCommand

from core.models import BarTabEntry


class Command(BaseCommand):
    help = (
        "One-time backfill for a bug fixed 2026-07-31 (live report: Hezzy's tab — "
        "KES 50 total, 40 paid via mpesa, 10 left owing — showed BOTH 40 and 10 as "
        "still owed once converted to debt). BarTabEntry.split_paid_unpaid_locked() "
        "(the partial-amount tab-settle split) always correctly marked the KEPT/PAID "
        "portion's BarTabEntry.is_paid=True and BarTabEntry.payment_method=<real "
        "method>, but never updated the underlying Transaction.payment_method away "
        "from 'credit' — which is what the debt tracker's credit_qs and "
        "shift_views._reconcile()'s cash/mpesa/credit totals both read directly, "
        "independent of BarTabEntry.is_paid. Every historical split-paid entry is "
        "therefore still permanently mis-tagged as an outstanding credit transaction "
        "and permanently missing from that shift's real cash/mpesa collected. "
        "Finds every BarTabEntry that IS marked paid via cash/mpesa but whose "
        "Transaction is still stuck on 'credit', and syncs the Transaction to match. "
        "Safe to re-run — only touches rows still in the broken state."
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
