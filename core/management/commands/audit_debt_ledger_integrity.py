from collections import defaultdict

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Business
from core.debt_views import _txn_tab_entry
from core.models import BarTab, Customer, Transaction


class Command(BaseCommand):
    help = (
        "Read-only diagnostic (2026-08-14 live report — Roy: 'do tabs that "
        "have been cleared somehow leak into the debt tracker as outstanding "
        "debt?'). Checks the three concrete, mechanical ways that could "
        "actually happen — never guesses at business logic, never changes "
        "any data:\n"
        "  1) A Transaction still tagged payment_method='credit' whose own "
        "     BarTabEntry is already is_paid=True — the direct signature of "
        "     a settle that updated the tab entry but never synced the "
        "     underlying Transaction, which is what the debt tracker "
        "     actually reads (Transaction.payment_method='credit').\n"
        "  2) A BarTab left status='SETTLED' with no customer_id set, still "
        "     carrying unpaid entries — a tab stuck in limbo that "
        "     settle_tab()'s debt-redirect can't recognise (it requires "
        "     tab.customer_id to route a payment into the debt ledger).\n"
        "  3) Two or more Customer records sharing the same name (case- and "
        "     whitespace-insensitive) within one business — a real payment "
        "     recorded against one duplicate never reduces the other's "
        "     outstanding figure, since CustomerDebtPayment is tied to one "
        "     specific Customer row, not a name string.\n"
        "Every finding needs a human decision to resolve (the existing "
        "'🔀 Sahihisha Jina la Mteja' customer-merge tool for #3, or a direct "
        "look at the specific transaction/tab for #1 and #2) — this command "
        "only surfaces candidates, it never mutates anything."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--business', type=str, default='',
            help='Business name to check (case-insensitive substring match). '
                 'Omit to check every business.',
        )
        parser.add_argument(
            '--customer', type=str, default='',
            help='Also print a full itemized unpaid-transaction breakdown for '
                 'every customer whose name contains this (case-insensitive) — '
                 'item, date, amount, originating tab id/status — so the owner '
                 'can cross-check it directly against what they remember. '
                 'Ignored when --all-customers is also given.',
        )
        parser.add_argument(
            '--all-customers', action='store_true',
            help='Print the same itemized breakdown for EVERY customer in the '
                 'matched business(es) who has a nonzero outstanding balance — '
                 'the whole business at once, not just one name, plus a grand '
                 'total at the end. Customers with nothing owed are skipped to '
                 'keep the output focused on what actually needs reviewing.',
        )

    def handle(self, *args, **options):
        from core.debt_views import _get_customer_debt_data

        biz_filter = options['business'].strip()
        businesses = Business.objects.all()
        if biz_filter:
            businesses = businesses.filter(name__icontains=biz_filter)
        if not businesses.exists():
            self.stdout.write(self.style.WARNING('No matching business found.'))
            return

        customer_filter = options['customer'].strip()
        all_customers = options['all_customers']
        total_findings = 0
        grand_total_outstanding = 0.0
        grand_total_customers = 0

        for business in businesses:
            findings_here = []

            # ── 1) Entry says paid, Transaction still says 'credit' ──────────
            credit_txns = Transaction.objects.filter(
                business=business, type='Issue', payment_method='credit',
            ).select_related('item')
            for txn in credit_txns:
                entry = _txn_tab_entry(txn)
                if entry is not None and entry.is_paid:
                    findings_here.append(
                        f"  ⚠ Transaction #{txn.id} ({txn.item.description}, "
                        f"KES {txn.revenue():,.0f}, dated {txn.date}, "
                        f"recipient='{txn.recipient}') is tab-entry #{entry.id} "
                        f"which is is_paid=True (paid via '{entry.payment_method}' "
                        f"on tab #{entry.tab_id}) — but the Transaction itself is "
                        f"still payment_method='credit', so the debt tracker will "
                        f"still count it as owed."
                    )

            # ── 2) SETTLED tab, no customer, still has unpaid entries ────────
            stuck_tabs = BarTab.objects.filter(
                business=business, status='SETTLED', customer__isnull=True,
            ).prefetch_related('entries')
            for tab in stuck_tabs:
                unpaid_count = sum(1 for e in tab.entries.all() if not e.is_paid)
                if unpaid_count:
                    findings_here.append(
                        f"  ⚠ BarTab #{tab.id} ('{tab.customer_name}') is SETTLED "
                        f"with no customer_id but still has {unpaid_count} unpaid "
                        f"entr(y/ies) worth KES {tab.unpaid_total():,.0f} — stuck; "
                        f"settle_tab()'s debt-redirect can't recognise it without "
                        f"a customer_id."
                    )

            # ── 3) Duplicate customer names (case/whitespace-insensitive) ────
            by_norm_name = defaultdict(list)
            for cust in Customer.objects.filter(business=business):
                key = ' '.join((cust.name or '').split()).lower()
                if key:
                    by_norm_name[key].append(cust)
            for key, dupe_custs in by_norm_name.items():
                if len(dupe_custs) > 1:
                    lines = [f"  ⚠ {len(dupe_custs)} Customer records share the name '{dupe_custs[0].name}':"]
                    for c in dupe_custs:
                        d = _get_customer_debt_data(c, business, scope='all')
                        lines.append(
                            f"      · Customer #{c.id} (phone={c.phone or '—'}): "
                            f"credit KES {d['total_credit']:,.0f}, paid KES "
                            f"{d['total_paid']:,.0f}, outstanding KES {d['outstanding']:,.0f}"
                        )
                    findings_here.append('\n'.join(lines))

            if findings_here:
                self.stdout.write(self.style.MIGRATE_HEADING(f'\n=== {business.name} ==='))
                for f in findings_here:
                    self.stdout.write(f)
                total_findings += len(findings_here)

            # ── optional: itemized breakdown for one/many customer(s) ────────
            if all_customers:
                custs = Customer.objects.filter(business=business).order_by('name')
            elif customer_filter:
                custs = Customer.objects.filter(
                    business=business, name__icontains=customer_filter,
                )
            else:
                custs = Customer.objects.none()

            for cust in custs:
                d = _get_customer_debt_data(cust, business, scope='all')
                if all_customers and d['outstanding'] <= 0:
                    continue  # whole-business mode: skip anyone with nothing owed
                self.stdout.write(self.style.MIGRATE_HEADING(
                    f"\n--- {business.name} / Customer #{cust.id} '{cust.name}' "
                    f"— outstanding KES {d['outstanding']:,.0f} ---"
                ))
                if not d['unpaid_transactions']:
                    self.stdout.write('  (nothing outstanding)')
                    continue
                grand_total_customers += 1
                grand_total_outstanding += d['outstanding']
                for row in d['unpaid_transactions']:
                    txn = row['txn']
                    entry = _txn_tab_entry(txn)
                    if entry is not None:
                        tab = entry.tab
                        opened = (
                            timezone.localtime(tab.opened_at).date()
                            if tab.opened_at else '?'
                        )
                        tab_info = f"tab #{tab.id} ({tab.status}, opened {opened})"
                    else:
                        tab_info = 'no tab (direct sale)'
                    self.stdout.write(
                        f"  · {txn.date} — {txn.item.description} — "
                        f"KES {row['amount']:,.0f} — {row['days_outstanding']} "
                        f"days outstanding — {tab_info}"
                    )

        if all_customers:
            self.stdout.write(self.style.MIGRATE_HEADING(
                f'\n=== TOTAL: {grand_total_customers} customer(s) with an '
                f'outstanding balance, KES {grand_total_outstanding:,.0f} combined ==='
            ))

        if total_findings:
            self.stdout.write(self.style.WARNING(
                f'\n{total_findings} finding(s) above need a human decision — '
                'nothing was changed by this command.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('\nNo integrity issues found.'))
