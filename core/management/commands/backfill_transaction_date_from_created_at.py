from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Transaction


class Command(BaseCommand):
    help = (
        "One-time backfill: Transaction.date and Transaction.created_at are two "
        "independently-defaulted fields meant to represent the same moment, but "
        "every backdating feature built across this session (Quick Sell's "
        "whole-cart catch-up toggle 2026-08-07, Kitchen Board's own portion/"
        "batch/bunch backdate 2026-08-09, and the direct-Deni backdate widening "
        "2026-08-12) only ever overrode created_at= — date= kept its own "
        "independent default of 'today, the day it was actually entered', so "
        "every backdated transaction silently landed under the WRONG date in "
        "any date=-filtered report (Daily Sales /daily/, the emailed daily "
        "summary, Transaction History's date filter). Fixed at the write side "
        "same day (2026-08-12) so this never recurs; this backfill corrects "
        "every historical row created before that fix. Safe to re-run — only "
        "touches rows where the two genuinely disagree (in the project's local "
        "timezone, matching every other 'today' computation in this app)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='List what would be corrected without saving anything.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        qs = Transaction.objects.filter(created_at__isnull=False).select_related(
            'business', 'item',
        ).order_by('id')

        count = 0
        for txn in qs.iterator(chunk_size=500):
            correct_date = timezone.localtime(txn.created_at).date()
            if txn.date == correct_date:
                continue
            biz_name = txn.business.name if txn.business else '(no business)'
            self.stdout.write(
                f"  [{biz_name}] txn #{txn.id} — {txn.item.description if txn.item_id else '?'}: "
                f"date {txn.date} -> {correct_date} (created_at {txn.created_at})"
            )
            if not dry_run:
                txn.date = correct_date
                txn.save(update_fields=['date'])
            count += 1

        if dry_run:
            self.stdout.write(self.style.WARNING(f"DRY RUN — would correct {count} transaction(s)."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Corrected {count} transaction(s)."))
