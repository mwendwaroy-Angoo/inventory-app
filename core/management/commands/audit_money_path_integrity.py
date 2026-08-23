from django.core.management.base import BaseCommand
from django.db.models import Q

from accounts.models import Business
from core.models import BarTabEntry, CustomerDebtPayment, TabPaymentRevocation


class Command(BaseCommand):
    help = (
        "READ-ONLY. Answers 'did the three 2026-08-24 money-path bugs actually "
        "touch my live data?' — changes NOTHING, so it is always safe to run.\n\n"
        "Checks the three concrete stored-state signatures those bugs leave "
        "behind:\n"
        "  (A) Revoked entry with a stale amount_paid — revoke_payment_locked() "
        "      used to leave amount_paid at the full amount, so the re-opened "
        "      entry read as owing KES 0 and could not be re-settled.\n"
        "  (B) A debt payment taken via STK from the public receipt page whose "
        "      coverage was never persisted onto the entry — that path had no "
        "      partial-coverage branch at all, so the customer's balance never "
        "      moved. Detected by comparing what the payment ledger says was "
        "      collected against what the entries actually record.\n"
        "  (C) Entries still missing debt_collected_amount (migration 0175 "
        "      default) — run backfill_debt_collected_amount to fix, needed for "
        "      revoke and till reconciliation to behave correctly on legacy rows.\n\n"
        "Findings are reported per business with enough detail to reconcile by "
        "hand. Nothing here is auto-repaired."
    )

    def add_arguments(self, parser):
        parser.add_argument('--business', type=str, default='', help='Business name substring (default: all).')

    def handle(self, *args, **options):
        businesses = Business.objects.all()
        if options['business']:
            businesses = businesses.filter(name__icontains=options['business'])
        if not businesses.exists():
            self.stdout.write(self.style.ERROR('No matching business.'))
            return

        grand_total = 0
        for business in businesses:
            findings = 0
            self.stdout.write(self.style.WARNING(f"\n=== [{business.name}] ==="))

            # ── (A) Revoked entries left with a stale amount_paid ──────────
            revoked_entry_ids = set(
                TabPaymentRevocation.objects.filter(business=business)
                .values_list('entry_id', flat=True)
            )
            stale = []
            if revoked_entry_ids:
                for entry in BarTabEntry.objects.filter(
                    id__in=revoked_entry_ids, is_paid=False, amount_paid__gt=0,
                ).select_related('tab', 'transaction'):
                    # Post-fix, a revoked entry's amount_paid should be exactly
                    # what the debt tracker collected. Anything above that is
                    # the stale counter portion the old code left behind.
                    expected = entry.debt_collected_amount or 0
                    if entry.amount_paid > expected:
                        stale.append((entry, expected))
            if stale:
                findings += len(stale)
                self.stdout.write(self.style.ERROR(
                    f"  (A) {len(stale)} revoked entr(ies) with a stale amount_paid "
                    f"— these currently read as owing less than they should:"
                ))
                for entry, expected in stale:
                    self.stdout.write(
                        f"      tab #{entry.tab_id} ({entry.tab.customer_name}) — "
                        f"{entry.description}: amount={entry.amount} "
                        f"amount_paid={entry.amount_paid} (should be {expected}) "
                        f"→ showing KES {entry.remaining_amount()} owed instead of "
                        f"KES {(entry.amount or 0) - expected}"
                    )
            else:
                self.stdout.write("  (A) ✓ no revoked entries with a stale amount_paid")

            # ── (B) Receipt-STK debt payments never applied to any entry ───
            # Those payments carry a distinctive notes prefix written by
            # mpesa_views._create_debt_payment_from_receipt.
            receipt_stk = list(
                CustomerDebtPayment.objects.filter(business=business, notes__contains='risiti ')
                .exclude(reverted=True).select_related('customer')
            )
            unapplied = []
            for pay in receipt_stk:
                if not pay.customer_id:
                    continue
                # How much does the entry ledger actually record as collected
                # for this customer's debt-converted tabs?
                recorded = sum(
                    float(e.amount if e.is_paid else e.amount_paid)
                    for e in BarTabEntry.objects.filter(
                        tab__business=business, tab__customer_id=pay.customer_id,
                        tab__status='SETTLED',
                    )
                )
                paid_total = sum(
                    float(p.amount_paid) for p in CustomerDebtPayment.objects.filter(
                        business=business, customer_id=pay.customer_id,
                    ).exclude(reverted=True)
                )
                if paid_total > recorded + 0.01:
                    unapplied.append((pay.customer, paid_total, recorded))
            # de-dupe per customer
            seen = set()
            unapplied = [u for u in unapplied if not (u[0].id in seen or seen.add(u[0].id))]
            if unapplied:
                findings += len(unapplied)
                self.stdout.write(self.style.ERROR(
                    f"  (B) {len(unapplied)} customer(s) whose receipt-STK debt payment "
                    f"may never have been applied to their entries:"
                ))
                for cust, paid_total, recorded in unapplied:
                    self.stdout.write(
                        f"      #{cust.id} {cust.name}: payments recorded KES {paid_total:,.2f}, "
                        f"but entries only reflect KES {recorded:,.2f} "
                        f"— gap KES {paid_total - recorded:,.2f}"
                    )
            else:
                self.stdout.write("  (B) ✓ no unapplied receipt-STK debt payments detected")

            # ── (C) Legacy rows missing debt_collected_amount ──────────────
            legacy = BarTabEntry.objects.filter(
                tab__business=business, amount_paid__gt=0, debt_collected_amount=0,
            ).filter(
                Q(transaction__payment_method='credit') | Q(transaction__was_credit=True),
                transaction__type='Issue',
            ).count()
            if legacy:
                findings += legacy
                self.stdout.write(self.style.ERROR(
                    f"  (C) {legacy} entr(ies) still missing debt_collected_amount "
                    f"— run: python manage.py backfill_debt_collected_amount"
                ))
            else:
                self.stdout.write("  (C) ✓ debt_collected_amount is populated where needed")

            grand_total += findings
            if not findings:
                self.stdout.write(self.style.SUCCESS("  → clean, nothing to reconcile"))

        self.stdout.write(self.style.WARNING(f"\n=== TOTAL findings across all businesses: {grand_total} ==="))
        if not grand_total:
            self.stdout.write(self.style.SUCCESS(
                "Live figures are already correct — the fixes are forward-looking "
                "only for this data, nothing needs reconciling."
            ))
