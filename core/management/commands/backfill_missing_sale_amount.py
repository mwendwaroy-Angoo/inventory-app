from django.core.management.base import BaseCommand
from django.db.models import Q

from accounts.models import Business
from core.models import Receipt, Transaction


class Command(BaseCommand):
    help = (
        "Repair for the 2026-08-30 live report (Roy, Monsoon Inn — a 'KC "
        "Pineapple 250 ML x2' credit sale showed KES 800 on the customer's "
        "own receipt but only KES 400 in the debt tracker for the SAME "
        "Transaction). Root cause: a PLAIN (non-preset, non-produce) item "
        "line on Quick Sell's checkout, and its STK-settlement sibling, "
        "left Transaction.sale_amount=None, relying on revenue()'s live "
        "item.selling_price x qty fallback — so raising an item's price "
        "AFTER a sale silently changes what that historical sale is worth "
        "forever, everywhere revenue() is read (the debt tracker's own "
        "Total Credit and per-row Amount Owed, analytics, _reconcile()/"
        "till_expected_cash() for a still cash/mpesa Issue line). Both "
        "checkout paths are now fixed to always pin sale_amount going "
        "forward — this command repairs the historical gap.\n\n"
        "For every Issue transaction with sale_amount still NULL, recovers "
        "the TRUE amount actually charged at sale time from whichever of "
        "these survives (both are frozen snapshots the checkout code has "
        "ALWAYS written correctly, unaffected by this bug):\n"
        "  1) A linked BarTabEntry's own .amount (tab/credit sales) — "
        "     guaranteed untouched-original if sale_amount is still NULL, "
        "     since every split mechanism in this app (split_paid_unpaid_"
        "     locked, split_kept_unpaid_locked) always writes a real "
        "     sale_amount onto the original transaction the moment it "
        "     touches entry.amount, so a still-NULL sale_amount means this "
        "     transaction was never split.\n"
        "  2) A matching Receipt.lines entry (by txn_id) for a direct, "
        "     tab-less sale.\n"
        "A transaction recoverable by neither is left untouched and listed "
        "separately -- revenue() keeps falling back to the live formula for "
        "those, same as before this command runs.\n\n"
        "Reports two different things for every business, since they need "
        "different follow-up: 'ALREADY DRIFTED' (the recovered historical "
        "value disagrees with what revenue() currently returns -- a real, "
        "live-right-now overstatement or understatement someone may already "
        "be looking at) vs 'not yet drifted' (recoverable and now pinned for "
        "the future, but nothing currently disagrees -- pinning it only "
        "prevents a FUTURE price edit from causing this). --dry-run first."
    )

    def add_arguments(self, parser):
        parser.add_argument('--business', type=str, default='', help='Business name substring. Omit for every business.')
        parser.add_argument('--dry-run', action='store_true', help='Report only, change nothing.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        biz_filter = options['business'].strip()

        businesses = Business.objects.all()
        if biz_filter:
            businesses = businesses.filter(name__icontains=biz_filter)
        if not businesses.exists():
            self.stdout.write(self.style.ERROR('No matching business found.'))
            return

        grand_recovered = 0
        grand_drifted = 0
        grand_drift_kes = 0.0
        grand_unrecoverable = 0

        for business in businesses:
            candidates = (
                Transaction.objects.filter(
                    business=business, type='Issue', sale_amount__isnull=True,
                )
                .exclude(Q(item__isnull=True))
                .select_related('item')
                .order_by('date', 'created_at')
            )
            if not candidates.exists():
                continue

            # Build a txn_id -> receipt line lookup once per business, for
            # recovering a direct (tab-less) sale's originally-charged
            # amount. Receipt.lines is a JSONField list -- no portable DB
            # filter across SQLite/Postgres for "contains this txn_id" (see
            # this app's own documented _safe_linked_query() history), so
            # this is a plain Python scan, done once, not per transaction.
            line_by_txn_id = {}
            for receipt in Receipt.objects.filter(business=business).only('id', 'lines'):
                for line in (receipt.lines or []):
                    tid = line.get('txn_id')
                    if tid is not None:
                        line_by_txn_id[tid] = line

            recovered_here = []
            unrecoverable_here = []
            for txn in candidates:
                entry = None
                try:
                    entry = txn.tab_entry
                except Exception:
                    entry = None
                if entry is not None:
                    recovered_here.append((txn, float(entry.amount), 'tab entry'))
                    continue
                line = line_by_txn_id.get(txn.id)
                if line is not None and line.get('subtotal') is not None:
                    recovered_here.append((txn, float(line['subtotal']), 'receipt line'))
                    continue
                unrecoverable_here.append(txn)

            if not recovered_here and not unrecoverable_here:
                continue

            self.stdout.write(self.style.MIGRATE_HEADING(f'\n=== {business.name} ==='))

            drifted_here = 0
            drift_kes_here = 0.0
            for txn, recovered_amount, source in recovered_here:
                current_live = txn.revenue()  # still the live fallback, sale_amount is NULL
                delta = round(recovered_amount - current_live, 2)
                if abs(delta) > 0.01:
                    drifted_here += 1
                    drift_kes_here += delta
                    self.stdout.write(self.style.ERROR(
                        f"  ⚠ ALREADY DRIFTED  txn#{txn.id} {txn.date} "
                        f"{txn.item.description if txn.item else '?'} — was KES "
                        f"{recovered_amount:,.2f} (from {source}), now silently "
                        f"reads KES {current_live:,.2f} (delta KES {delta:+,.2f}) — "
                        f"recipient={txn.recipient!r} payment_method={txn.payment_method!r}"
                    ))
                if not dry_run:
                    txn.sale_amount = recovered_amount
                    txn.save(update_fields=['sale_amount'])

            for txn in unrecoverable_here:
                self.stdout.write(
                    f"  · not recoverable: txn#{txn.id} {txn.date} "
                    f"{txn.item.description if txn.item else '?'} — no tab entry, "
                    f"no matching receipt line. Left as-is; revenue() keeps using "
                    f"the live item.selling_price fallback for this one."
                )

            self.stdout.write(
                f"  {len(recovered_here)} recovered ({drifted_here} already "
                f"drifted, KES {drift_kes_here:+,.2f}), {len(unrecoverable_here)} "
                f"not recoverable."
            )
            grand_recovered += len(recovered_here)
            grand_drifted += drifted_here
            grand_drift_kes += drift_kes_here
            grand_unrecoverable += len(unrecoverable_here)

        self.stdout.write(self.style.WARNING(
            f"\n=== TOTAL: {grand_recovered} transaction(s) recovered "
            f"({grand_drifted} already drifted, net KES {grand_drift_kes:+,.2f}), "
            f"{grand_unrecoverable} not recoverable ==="
        ))
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — nothing was saved. Re-run without --dry-run to apply.'))
        else:
            self.stdout.write(self.style.SUCCESS('sale_amount backfilled for every recoverable transaction above.'))
