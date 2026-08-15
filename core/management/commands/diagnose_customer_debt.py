from django.core.management.base import BaseCommand

from accounts.models import Business
from core.models import BarTab, Customer, CustomerDebtPayment, Transaction


class Command(BaseCommand):
    help = (
        "READ-ONLY. Diagnostic for the 2026-08-15 'Paid exceeds Credit' live report "
        "(see Transaction.was_credit's own docstring for the full mechanism) — shows "
        "the RAW, underlying transaction and payment history for a customer, alongside "
        "the debt tracker's own CURRENT (post-fix) Total Credit figure, so a human can "
        "verify the fix actually repaired a given customer or spot one it didn't. "
        "Changes NOTHING.\n\n"
        "--customer=NAME shows one customer, full detail.\n"
        "--all-customers scans every customer in the matched business(es): a brief "
        "one-line summary for each, full detail printed ONLY for anyone still showing "
        "a mismatch (Total Paid exceeding the fixed Total Credit) — genuine, "
        "already-resolved historical debt from before this fix existed, which is NOT "
        "automatically repaired (backfill_was_credit, which tried to, has been "
        "retired — it could not reliably tell genuine debt apart from an ordinary "
        "tab). Anyone still flagged here needs manual reconciliation via "
        "record_debt_payment's own backdate field."
    )

    def add_arguments(self, parser):
        parser.add_argument('--business', type=str, required=True, help='Business name substring.')
        parser.add_argument('--customer', type=str, help='Customer name (exact, case-insensitive).')
        parser.add_argument('--all-customers', action='store_true', help='Scan every customer instead of one.')

    def handle(self, *args, **options):
        businesses = Business.objects.filter(name__icontains=options['business'])
        if not businesses.exists():
            self.stdout.write(self.style.ERROR('No matching business.'))
            return

        if options['all_customers']:
            self._scan_all(businesses)
            return

        if not options['customer']:
            self.stdout.write(self.style.ERROR('Pass --customer=NAME or --all-customers.'))
            return

        for business in businesses:
            customers = Customer.objects.filter(business=business, name__iexact=options['customer'])
            if not customers.exists():
                self.stdout.write(f"[{business.name}] No customer named '{options['customer']}'.")
                continue
            for customer in customers:
                self._diagnose_one(business, customer, verbose=True)

    def _scan_all(self, businesses):
        from core.debt_views import _get_customer_debt_data

        flagged = 0
        clean = 0
        for business in businesses:
            customers = Customer.objects.filter(business=business).order_by('name')
            if not customers.exists():
                self.stdout.write(f"[{business.name}] No customers.")
                continue
            self.stdout.write(self.style.WARNING(f"\n=== [{business.name}] Scanning {customers.count()} customer(s) ==="))
            for customer in customers:
                raw_paid = sum(
                    float(p.amount_paid)
                    for p in CustomerDebtPayment.objects.filter(business=business, customer=customer)
                )
                if raw_paid == 0:
                    continue  # never made a debt payment — nothing to check
                data = _get_customer_debt_data(customer, business)
                fixed_total_credit = data['total_credit']
                mismatch = raw_paid > fixed_total_credit + 0.01  # small float-rounding tolerance
                if mismatch:
                    flagged += 1
                    self.stdout.write(self.style.ERROR(
                        f"  ⚠️  #{customer.id} {customer.name}: Total Paid KES {raw_paid:.2f} "
                        f"still EXCEEDS fixed Total Credit KES {fixed_total_credit:.2f} — "
                        f"gap KES {raw_paid - fixed_total_credit:.2f}"
                    ))
                    self._diagnose_one(business, customer, verbose=True)
                else:
                    clean += 1
                    self.stdout.write(
                        f"  ✓ #{customer.id} {customer.name}: Total Credit KES {fixed_total_credit:.2f}, "
                        f"Total Paid KES {raw_paid:.2f}, Outstanding KES {data['outstanding']:.2f}"
                    )

        self.stdout.write(self.style.WARNING(
            f"\n=== TOTAL: {flagged} customer(s) still mismatched, {clean} clean ==="
        ))
        if flagged:
            self.stdout.write(
                "No automatic repair for these — reconcile each manually using the raw "
                "detail printed above and record_debt_payment's own backdate field if "
                "needed. These are all genuine, already-resolved historical debt from "
                "before the fix existed; there's no safe way to auto-recover them."
            )

    def _diagnose_one(self, business, customer, verbose=True):
        from core.debt_views import _get_customer_debt_data

        self.stdout.write(self.style.WARNING(
            f"\n=== [{business.name}] Customer #{customer.id}: {customer.name} ==="
        ))

        txns = (
            Transaction.objects.filter(business=business, recipient=customer.name, type='Issue')
            .select_related('item', 'item__store')
            .order_by('date', 'created_at')
        )
        self.stdout.write(f"\n-- Every Issue transaction ever recorded for this name ({txns.count()}) --")
        for t in txns:
            station = 'kitchen' if (t.item and t.item.store and t.item.store.is_kitchen) else 'bar'
            tab_entry = None
            try:
                tab_entry = t.tab_entry
            except Exception:
                pass
            tab_state = ''
            if tab_entry is not None:
                tab_state = f" | tab#{tab_entry.tab_id} status={tab_entry.tab.status} entry.is_paid={tab_entry.is_paid}"
            self.stdout.write(
                f"  txn#{t.id} {t.date} [{station}] {t.item.description if t.item else '?'} "
                f"KES {t.revenue()} payment_method={t.payment_method!r} was_credit={t.was_credit}{tab_state}"
            )

        payments = CustomerDebtPayment.objects.filter(
            business=business, customer=customer,
        ).order_by('paid_at')
        self.stdout.write(f"\n-- Every CustomerDebtPayment ever recorded ({payments.count()}) --")
        total_paid = 0.0
        for p in payments:
            total_paid += float(p.amount_paid)
            self.stdout.write(
                f"  {p.paid_at} [{p.source}] KES {p.amount_paid} recorded_by="
                f"{p.recorded_by.username if p.recorded_by_id else '-'} notes={p.notes!r}"
            )
        self.stdout.write(f"  TOTAL PAID (raw sum): KES {total_paid:.2f}")

        data = _get_customer_debt_data(customer, business)
        self.stdout.write(self.style.WARNING(
            f"\n-- Debt tracker's CURRENT (post-fix) figures: Total Credit KES "
            f"{data['total_credit']:.2f}, Total Paid KES {data['total_paid']:.2f}, "
            f"Outstanding KES {data['outstanding']:.2f} --"
        ))
        if total_paid > data['total_credit'] + 0.01:
            self.stdout.write(self.style.ERROR(
                "  ⚠️  Total Paid still exceeds Total Credit — genuine historical debt "
                "resolved before this fix existed, with no safe automatic repair. "
                "Reconcile manually with the raw transaction list above."
            ))

        open_tabs = BarTab.objects.filter(business=business, customer=customer, status='OPEN')
        if open_tabs.exists():
            self.stdout.write(self.style.ERROR(
                f"\n-- {open_tabs.count()} OPEN tab(s) not yet converted to debt (correctly excluded above) --"
            ))
            for tab in open_tabs:
                unpaid = tab.entries.filter(is_paid=False)
                for e in unpaid:
                    self.stdout.write(f"  tab#{tab.id} entry: {e.description} KES {e.amount} (still OPEN, not debt yet)")
