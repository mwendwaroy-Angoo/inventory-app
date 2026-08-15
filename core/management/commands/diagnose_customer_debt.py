from django.core.management.base import BaseCommand

from accounts.models import Business
from core.models import Customer, CustomerDebtPayment, Transaction


class Command(BaseCommand):
    help = (
        "READ-ONLY. Emergency diagnostic for the 2026-08-14 'Paid exceeds Credit' "
        "live report — shows the RAW, underlying transaction and payment history "
        "for one customer, bypassing _get_customer_debt_data()'s aggregate math "
        "entirely, so a human can see the true picture directly while the "
        "aggregate-computation bug is being fixed. Changes NOTHING."
    )

    def add_arguments(self, parser):
        parser.add_argument('--business', type=str, required=True, help='Business name substring.')
        parser.add_argument('--customer', type=str, required=True, help='Customer name (exact, case-insensitive).')

    def handle(self, *args, **options):
        businesses = Business.objects.filter(name__icontains=options['business'])
        if not businesses.exists():
            self.stdout.write(self.style.ERROR('No matching business.'))
            return

        for business in businesses:
            customers = Customer.objects.filter(business=business, name__iexact=options['customer'])
            if not customers.exists():
                self.stdout.write(f"[{business.name}] No customer named '{options['customer']}'.")
                continue

            for customer in customers:
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
                        f"KES {t.revenue()} payment_method={t.payment_method!r}{tab_state}"
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

                still_credit_total = sum(
                    float(t.revenue()) for t in txns if t.payment_method == 'credit'
                )
                self.stdout.write(self.style.WARNING(
                    f"\n-- Sum of transactions STILL currently payment_method='credit': KES {still_credit_total:.2f} --"
                ))
                self.stdout.write(
                    "  (This is what the debt tracker's 'Total Credit' figure currently shows — "
                    "it does NOT include transactions resolved directly at the counter without "
                    "a matching payment record, even though those were genuinely paid.)"
                )

                from core.models import BarTab
                open_tabs = BarTab.objects.filter(business=business, customer=customer, status='OPEN')
                if open_tabs.exists():
                    self.stdout.write(self.style.ERROR(
                        f"\n-- {open_tabs.count()} OPEN tab(s) not yet converted to debt (correctly excluded above) --"
                    ))
                    for tab in open_tabs:
                        unpaid = tab.entries.filter(is_paid=False)
                        for e in unpaid:
                            self.stdout.write(f"  tab#{tab.id} entry: {e.description} KES {e.amount} (still OPEN, not debt yet)")
