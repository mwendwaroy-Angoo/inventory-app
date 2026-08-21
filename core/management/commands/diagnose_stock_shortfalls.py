from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

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
        "exceptions and reports which ones have a live, uncorrected gap.\n\n"
        "2026-08-21 same-item follow-up (Roy — 'staff physically counted 8 Dallas "
        "bottles but the system showed 16, I need to know if the owner received "
        "double via the latest receipt, or if there were unrecorded sales'): "
        "--item mode now also flags two Receipt transactions close together in time "
        "with matching/similar quantities (the concrete signature of a duplicate "
        "delivery entry), and --physical=N prints the exact system-vs-physical gap "
        "with a plain-language explanation of what each direction of gap implies. "
        "The same ledger this prints is also now shown directly in the app, per "
        "item, at /stock/<item_id>/ (Item.balance_journey())."
    )

    def add_arguments(self, parser):
        parser.add_argument('--business', type=str, required=True, help='Business name substring.')
        parser.add_argument('--item', type=str, help='Item description (exact, case-insensitive).')
        parser.add_argument('--all-items', action='store_true', help='Scan every item with a shrinkage exception.')
        parser.add_argument(
            '--physical', type=float,
            help='The real physical count just taken — prints the exact gap vs the system balance (--item mode only).',
        )

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
                self._diagnose_one(business, item, physical=options.get('physical'))

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

    def _diagnose_one(self, business, item, physical=None):
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

        # 2026-08-21 live report (Roy): "Blue Ice" balance/units never land on
        # a clean quarter-bottle multiple the way "KC Ginger" does. Each
        # preset-attributed sale's Transaction.qty is a PERMANENT SNAPSHOT of
        # `preset.quantity_consumed * cart_qty` taken at sale time (core/
        # views.py's quick_sell() checkout) — but the preset's own
        # quantity_consumed is read LIVE, never versioned. If a preset's
        # fraction is ever edited (e.g. "Double" changed from 0.25 to 0.2)
        # after some sales already happened under the old value, every
        # OLDER transaction's qty stays permanently based on the fraction
        # that existed then, while newer sales use the new one — the running
        # balance becomes an honest sum of two different schemes, which
        # will almost never land on a clean multiple of either. This isn't
        # a bug in the sum itself (each individual sale WAS correctly priced
        # and deducted for the fraction that was configured at the time) —
        # it's a real, permanent record of a preset that changed shape
        # mid-history. Flags any transaction whose |qty| isn't a clean
        # integer multiple of its preset's CURRENT quantity_consumed.
        preset_drift = []
        for t in txns:
            if t.preset_id and t.preset and t.preset.quantity_consumed:
                qc = t.preset.quantity_consumed
                remainder = abs(t.qty or 0) % qc if qc else None
                if remainder is not None and remainder > Decimal('0.0001') and (qc - remainder) > Decimal('0.0001'):
                    preset_drift.append((t, qc, remainder))
        if preset_drift:
            self.stdout.write(self.style.ERROR(
                f"\n-- ⚠️  {len(preset_drift)} transaction(s) don't divide evenly by their "
                "preset's CURRENT quantity_consumed — this preset's fraction was very "
                "likely edited after these sales already happened. Each was correctly "
                "priced/deducted for whatever fraction was configured AT THE TIME — this "
                "is why the running balance doesn't land on a clean multiple today: --"
            ))
            for t, qc, remainder in preset_drift[:20]:
                self.stdout.write(
                    f"  txn#{t.id} {t.date} preset={t.preset.label!r} qty={t.qty} "
                    f"(current quantity_consumed={qc}, remainder={remainder})"
                )
            if len(preset_drift) > 20:
                self.stdout.write(f"  ... and {len(preset_drift) - 20} more")

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

        # ── Duplicate-receipt heuristic (2026-08-21) ────────────────────────
        # "was the owner receiving double" — two Receipts close together in
        # time with a matching/near-matching qty is the concrete signature
        # of one physical delivery entered into the system twice.
        self.stdout.write(self.style.WARNING("\n-- Possible duplicate receipts --"))
        receipts = [(t, timezone.localtime(t.created_at)) for t in txns if t.type == 'Receipt' and t.created_at]
        found_dup = False
        for i in range(len(receipts)):
            t1, w1 = receipts[i]
            for j in range(i + 1, len(receipts)):
                t2, w2 = receipts[j]
                gap_hours = abs((w2 - w1).total_seconds()) / 3600
                if gap_hours > 48:
                    continue
                same_qty = t1.qty == t2.qty
                similar_qty = t1.qty != 0 and abs(t1.qty - t2.qty) / abs(t1.qty) <= Decimal('0.1')
                if same_qty or similar_qty:
                    found_dup = True
                    self.stdout.write(self.style.ERROR(
                        f"  ⚠️  txn#{t1.id} ({w1}, qty={t1.qty}, invoice={t1.invoice_no or '-'}) and "
                        f"txn#{t2.id} ({w2}, qty={t2.qty}, invoice={t2.invoice_no or '-'}) are "
                        f"{gap_hours:.1f}h apart with {'matching' if same_qty else 'similar'} "
                        f"quantities — check whether this was one delivery entered twice."
                    ))
        if not found_dup:
            self.stdout.write("  None found — no two Receipts close together with matching/similar qty.")

        # ── Physical-count gap (2026-08-21) ─────────────────────────────────
        if physical is not None:
            current = item.current_balance()
            gap = current - Decimal(str(physical))
            self.stdout.write(self.style.WARNING(
                f"\n-- Physical count given: {physical} {item.unit} vs system {current} {item.unit} --"
            ))
            if gap == 0:
                self.stdout.write(self.style.SUCCESS("  ✓ System matches physical count exactly."))
            elif gap > 0:
                self.stdout.write(self.style.ERROR(
                    f"  System shows {gap} {item.unit} MORE than what's physically there. Either "
                    f"(a) a Receipt above didn't reflect a real delivery — check the duplicate-"
                    f"receipt flag above, or a receipt entered against the wrong item, or "
                    f"(b) real sales/wastage happened that were never recorded as a Transaction "
                    f"— an unrecorded sale leaves NO trace in this ledger at all, so its ABSENCE, "
                    f"not a wrong entry, is what to look for (ask staff directly whether every "
                    f"sale of this item went through Quick Sell/Bar Board)."
                ))
            else:
                self.stdout.write(self.style.ERROR(
                    f"  System shows {abs(gap)} {item.unit} FEWER than what's physically there — "
                    f"a real receipt/return was never recorded, or an earlier Rekebisha "
                    f"overcorrected downward."
                ))
