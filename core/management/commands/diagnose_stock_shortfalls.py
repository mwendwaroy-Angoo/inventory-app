from django.core.management.base import BaseCommand

from accounts.models import Business
from core.models import BusinessException, Item, Transaction


class Command(BaseCommand):
    help = (
        "READ-ONLY. Diagnostic for the 2026-08-19 live report (Roy, Monsoon Inn) — "
        "staff claim they recorded every sale correctly, yet the app shows stock for "
        "an item (Dallas, KC Ginger) that physically no longer exists. Traces the "
        "most likely code-level mechanism: Item.capped_deduction() (built 2026-08-07 "
        "at Roy's own request — 'negative balances should never be there') silently "
        "floors a sale's stock deduction at whatever balance is CURRENTLY on record "
        "whenever an M-Pesa/STK settlement, a reconciled manual payment, or a "
        "waitress table-order SERVED conversion runs AFTER the balance has already "
        "dropped too low to cover it — the shortfall is never actually deducted, only "
        "flagged as a BusinessException(kind='shrinkage') for the owner to notice. If "
        "that exception feed goes unwatched, the item's recorded balance stays "
        "permanently higher than physical reality by exactly the shortfall amount, "
        "with NO marker on the Transaction itself distinguishing a capped sale from "
        "an ordinary one — this command cross-references the exception feed to make "
        "that visible again. Changes NOTHING.\n\n"
        "--item=NAME shows one item's full transaction ledger + every shrinkage "
        "exception ever flagged for it, in one place.\n"
        "--all-items scans every item in the business for unacknowledged shrinkage "
        "exceptions and reports which ones have a live, uncorrected gap."
    )

    def add_arguments(self, parser):
        parser.add_argument('--business', type=str, required=True, help='Business name substring.')
        parser.add_argument('--item', type=str, help='Item description (exact, case-insensitive).')
        parser.add_argument('--all-items', action='store_true', help='Scan every item with a shrinkage exception.')

    def handle(self, *args, **options):
        businesses = Business.objects.filter(name__icontains=options['business'])
        if not businesses.exists():
            self.stdout.write(self.style.ERROR('No matching business.'))
            return

        if options['all_items']:
            for business in businesses:
                self._scan_all(business)
            return

        if not options['item']:
            self.stdout.write(self.style.ERROR('Pass --item=NAME or --all-items.'))
            return

        for business in businesses:
            items = Item.objects.filter(business=business, description__iexact=options['item'])
            if not items.exists():
                self.stdout.write(f"[{business.name}] No item named '{options['item']}'.")
                continue
            for item in items:
                self._diagnose_one(business, item)

    def _scan_all(self, business):
        self.stdout.write(self.style.WARNING(f"\n=== [{business.name}] Shrinkage exceptions ==="))
        exceptions = BusinessException.objects.filter(
            business=business, kind='shrinkage',
        ).order_by('-created_at')
        if not exceptions.exists():
            self.stdout.write("  None ever flagged for this business.")
            return
        unacked = exceptions.filter(acknowledged_at__isnull=True)
        self.stdout.write(
            f"  {exceptions.count()} total, {unacked.count()} never acknowledged "
            f"(acknowledging does NOT correct the balance — it only marks the "
            f"notification as seen; only a Rekebisha correction changes the number)."
        )
        for exc in exceptions:
            ack = f"acked by {exc.acknowledged_by} on {exc.acknowledged_at}" if exc.acknowledged_at else "UNACKNOWLEDGED"
            self.stdout.write(f"\n  [{exc.created_at}] {exc.title}")
            self.stdout.write(f"    {exc.detail}")
            self.stdout.write(f"    ({ack})")

    def _diagnose_one(self, business, item):
        self.stdout.write(self.style.WARNING(
            f"\n=== [{business.name}] Item #{item.id}: {item.description} ({item.unit}) ==="
        ))
        self.stdout.write(
            f"  Recorded balance (current_balance()): {item.current_balance()} {item.unit}\n"
            f"  Reorder level: {item.reorder_level}   Cost price: {item.cost_price}"
        )

        txns = (
            Transaction.objects.filter(business=business, item=item)
            .select_related('preset', 'keg_barrel', 'produce_bunch', 'kitchen_batch')
            .order_by('date', 'created_at', 'id')
        )
        self.stdout.write(f"\n-- Every transaction ever recorded against this item ({txns.count()}) --")
        running = item.opening_bin_balance
        for t in txns:
            running += (t.qty or 0)
            preset_label = f" preset={t.preset.label!r}" if t.preset_id else ""
            self.stdout.write(
                f"  txn#{t.id} {t.date} {t.type:<7} qty={t.qty:>8} -> running={running:>8} "
                f"payment_method={t.payment_method or '-':<7}{preset_label} "
                f"recorded_by={t.recorded_by.username if t.recorded_by_id else '-'} "
                f"invoice_no={t.invoice_no!r}"
            )

        exceptions = BusinessException.objects.filter(
            business=business, kind='shrinkage', title__icontains=item.description,
        ).order_by('created_at')
        self.stdout.write(f"\n-- Shrinkage exceptions ever flagged for this item ({exceptions.count()}) --")
        if not exceptions.exists():
            self.stdout.write(
                "  None. If the balance is still wrong, capped_deduction() is likely "
                "NOT the cause here — check for: (1) a duplicate Item with the same "
                "name (compare material_no across ALL items named "
                f"'{item.description}' in this business), (2) unrecorded wastage/"
                "breakage, (3) a sale recorded via Add Transaction's plain Qty field "
                "instead of Quick Sell's preset tiles (Add Transaction has no preset "
                "picker for a non-produce item — a tot/half sale typed there as "
                "whole units under-deducts, the exact same visible symptom with a "
                "different, non-code cause)."
            )
        else:
            for exc in exceptions:
                ack = f"acked by {exc.acknowledged_by} on {exc.acknowledged_at}" if exc.acknowledged_at else "UNACKNOWLEDGED"
                self.stdout.write(f"\n  [{exc.created_at}] {exc.title}")
                self.stdout.write(f"    {exc.detail}")
                self.stdout.write(f"    ({ack})")
            self.stdout.write(self.style.ERROR(
                f"\n  This item has {exceptions.count()} recorded shortfall(s) — every one of "
                "them represents a sale where the app deliberately under-deducted stock "
                "(capped at the balance available then, per Roy's own 2026-08-07 "
                "'never go negative' request) rather than reflect the true physical "
                "amount sold. This is the most likely explanation for 'the app says "
                "there is stock when physically there is none' for this item."
            ))

        others = Item.objects.filter(business=business, description__iexact=item.description).exclude(id=item.id)
        if others.exists():
            self.stdout.write(self.style.ERROR(
                f"\n  ⚠️  {others.count()} OTHER item(s) also named '{item.description}' in this "
                "business — a duplicate Item record is a separate, common cause of this "
                "exact symptom (a sale draws down one Item while Stock List displays the "
                "other's balance). Compare material_no/store below:"
            ))
            for o in others:
                self.stdout.write(f"    item#{o.id} material_no={o.material_no!r} store={o.store} balance={o.current_balance()}")
