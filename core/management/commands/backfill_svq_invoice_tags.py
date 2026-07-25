from django.core.management.base import BaseCommand

from core.models import StockVarianceQuery


class Command(BaseCommand):
    help = (
        "One-time backfill: tag pre-existing stock-take-variance corrective "
        "Issue transactions with invoice_no='[SVQ]', so historical corrections "
        "made before the 2026-07-25 fix are retroactively excluded from the "
        "live-revenue dashboard tiles the same way new ones already are. "
        "Safe to re-run — only touches rows with a corrective_txn set and a "
        "currently-blank invoice_no; run once per deployed environment."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='List what would be tagged without saving anything.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        qs = (
            StockVarianceQuery.objects.filter(
                corrective_txn__isnull=False,
                corrective_txn__type='Issue',
                corrective_txn__payment_method__in=['cash', 'mpesa', 'credit'],
            )
            .exclude(corrective_txn__invoice_no='[SVQ]')
            .select_related('corrective_txn', 'stock_take__business')
        )

        count = 0
        for svq in qs:
            txn = svq.corrective_txn
            self.stdout.write(
                f"  [{svq.stock_take.business.name}] {svq.item_name_cache} — "
                f"KES {txn.sale_amount or 0} on {txn.date} (txn #{txn.id})"
            )
            if not dry_run:
                txn.invoice_no = '[SVQ]'
                txn.save(update_fields=['invoice_no'])
            count += 1

        if dry_run:
            self.stdout.write(self.style.WARNING(f"DRY RUN — would tag {count} transaction(s)."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Tagged {count} transaction(s) with [SVQ]."))
