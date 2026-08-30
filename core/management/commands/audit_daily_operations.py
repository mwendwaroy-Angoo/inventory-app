import datetime

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db.models import Count, Q, Sum
from django.utils import timezone

from accounts.models import Business
from core.models import (
    BusinessExpense, Item, KegWeightReading, KitchenStockReceipt,
    PettyCash, PortioningEvent, Shift, StockVarianceQuery, Transaction,
)

SECTION_CHOICES = [
    'sales', 'shifts', 'stock', 'variances', 'receiving',
    'expenses', 'corrections', 'deep', 'all',
]


class Command(BaseCommand):
    help = (
        "READ-ONLY. One-shot consolidated audit of every transactional/service "
        "process for one business on one day — cash/mpesa/credit sales tie-out, "
        "per-shift reconciliation, stock movement + negative-balance integrity, "
        "stock-take variances, receiving events, expenses, and a corrections "
        "visibility summary — plus an optional --deep pass orchestrating the "
        "existing diagnose_recent_sales_visibility / audit_debt_ledger_integrity "
        "/ audit_money_path_integrity commands. Changes NOTHING.\n\n"
        "2026-08-30 live follow-up (Roy, Monsoon Inn): a mobile terminal makes "
        "a long scroll expensive to screenshot — default output is now COMPACT "
        "(totals + flags only, no per-transaction/per-staff/per-entry listing); "
        "pass --verbose for the full itemized detail, and --section=X to scope "
        "to exactly one section (e.g. re-run just the one that showed a flag, "
        "with --verbose, instead of the whole report again). --deep itself "
        "calls audit_debt_ledger_integrity WITHOUT --all-customers — that flag "
        "dumps a full itemized unpaid list for every customer with a balance "
        "(55 customers on a real run) and is its own separately-runnable, "
        "deliberately verbose tool; --deep only surfaces the short anomaly "
        "findings, which is what answers 'is anything wrong'.\n\n"
        "Usage: python manage.py audit_daily_operations --business=\"Monsoon Inn\" "
        "[--date=YYYY-MM-DD, default: yesterday] [--section=sales|shifts|stock|"
        "variances|receiving|expenses|corrections|deep|all, default: all] "
        "[--verbose] [--deep (only meaningful with --section=all; --section=deep "
        "always runs it)]"
    )

    def add_arguments(self, parser):
        parser.add_argument('--business', type=str, required=True, help='Business name substring.')
        parser.add_argument('--date', type=str, help='YYYY-MM-DD, local calendar day. Default: yesterday.')
        parser.add_argument(
            '--section', type=str, default='all', choices=SECTION_CHOICES,
            help="Run only one section instead of the full report. Default: all.",
        )
        parser.add_argument(
            '--verbose', action='store_true',
            help="Show full itemized detail (per-transaction/per-staff/per-entry) "
                 "instead of the default compact totals-and-flags-only output.",
        )
        parser.add_argument(
            '--deep', action='store_true',
            help="When --section=all, also run the three orchestrated deeper "
                 "structural checks (off by default to keep the report short). "
                 "Always runs when --section=deep.",
        )

    def handle(self, *args, **options):
        businesses = Business.objects.filter(name__icontains=options['business'])
        if not businesses.exists():
            self.stdout.write(self.style.ERROR('No matching business.'))
            return

        if options.get('date'):
            try:
                sel_date = datetime.datetime.strptime(options['date'], '%Y-%m-%d').date()
            except ValueError:
                self.stdout.write(self.style.ERROR('--date must be YYYY-MM-DD'))
                return
        else:
            sel_date = timezone.localdate() - datetime.timedelta(days=1)

        day_start = timezone.make_aware(datetime.datetime.combine(sel_date, datetime.time.min))
        day_end = timezone.make_aware(datetime.datetime.combine(sel_date, datetime.time.max))
        section = options['section']
        verbose = options['verbose']
        run_all = section == 'all'

        for business in businesses:
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"[{business.name}] — {sel_date.isoformat()}"
                + (f" — [{section}]" if not run_all else "")
            ))

            if run_all or section == 'sales':
                self._section_sales(business, day_start, day_end, verbose)
            if run_all or section == 'shifts':
                self._section_shifts(business, day_start, day_end, verbose)
            if run_all or section == 'stock':
                self._section_stock(business, day_start, day_end, verbose)
            if run_all or section == 'variances':
                self._section_variances(business, day_start, day_end, verbose)
            if run_all or section == 'receiving':
                self._section_receiving(business, day_start, day_end, verbose)
            if run_all or section == 'expenses':
                self._section_expenses(business, day_start, day_end, verbose)
            if run_all or section == 'corrections':
                self._section_corrections(business, day_start, day_end, verbose)

            if section == 'deep' or (run_all and options['deep']):
                self.stdout.write(self.style.MIGRATE_HEADING(
                    f"--- deep checks [{business.name}] (whole ledger, not date-scoped) ---"
                ))
                call_command(
                    'diagnose_recent_sales_visibility',
                    business=business.name, date=sel_date.isoformat(), stdout=self.stdout,
                )
                # 2026-08-30, second same-day follow-up: deliberately NOT
                # --all-customers here — that flag dumps a full itemized
                # unpaid-transaction list for EVERY customer with a balance
                # (55 customers on a real Monsoon Inn run), reintroducing the
                # exact "too much to screenshot" problem this whole redesign
                # was for. Without it, this only prints the short anomaly
                # findings (unsynced payment_method / stuck SETTLED tab /
                # duplicate names) — a handful of lines unless something is
                # actually wrong, which is the "is anything wrong" signal
                # --deep is for. Run audit_debt_ledger_integrity directly
                # with --all-customers (or --customer=NAME) if the full
                # itemized ledger dump is genuinely needed.
                call_command(
                    'audit_debt_ledger_integrity',
                    business=business.name, stdout=self.stdout,
                )
                call_command('audit_money_path_integrity', business=business.name, stdout=self.stdout)
            elif run_all:
                self.stdout.write(
                    "  (deep structural checks skipped — add --deep or run "
                    "--section=deep to include them)"
                )

            self.stdout.write(self.style.SUCCESS(f"[{business.name}] done."))

    # ── [1] Sales ────────────────────────────────────────────────────────────
    def _section_sales(self, business, day_start, day_end, verbose):
        self.stdout.write(self.style.WARNING("[1] SALES"))
        txns = list(
            Transaction.objects.filter(
                business=business, type='Issue',
                created_at__gte=day_start, created_at__lte=day_end,
            )
            .exclude(payment_method='void')
            .select_related('item__store', 'recorded_by')
        )
        if not txns:
            self.stdout.write("  none")
            return

        def bucket(is_kitchen_wanted):
            cash = mpesa = credit = 0.0
            for t in txns:
                is_kitchen = bool(t.item and t.item.store and t.item.store.is_kitchen)
                if is_kitchen != is_kitchen_wanted:
                    continue
                rev = float(t.revenue() or 0)
                if t.payment_method == 'cash':
                    cash += rev
                elif t.payment_method == 'mpesa':
                    mpesa += rev
                elif t.payment_method == 'credit':
                    credit += rev
            return cash, mpesa, credit

        bar_cash, bar_mpesa, bar_credit = bucket(False)
        kit_cash, kit_mpesa, kit_credit = bucket(True)
        total_cash = bar_cash + kit_cash
        total_mpesa = bar_mpesa + kit_mpesa
        total_credit = bar_credit + kit_credit

        self.stdout.write(
            f"  Bar cash/mpesa/credit: {bar_cash:.0f}/{bar_mpesa:.0f}/{bar_credit:.0f}  |  "
            f"Kitchen: {kit_cash:.0f}/{kit_mpesa:.0f}/{kit_credit:.0f}"
        )
        self.stdout.write(
            f"  TOTAL cash={total_cash:.0f} mpesa={total_mpesa:.0f} credit={total_credit:.0f} "
            f"confirmed={total_cash + total_mpesa:.0f} grand_total={total_cash + total_mpesa + total_credit:.0f}"
        )

        by_staff = {}
        for t in txns:
            if t.recorded_by:
                name = t.recorded_by.get_full_name() or t.recorded_by.username
            else:
                name = '(no recorded_by)'
            d = by_staff.setdefault(name, {'cash': 0.0, 'mpesa': 0.0, 'credit': 0.0, 'count': 0})
            rev = float(t.revenue() or 0)
            if t.payment_method in ('cash', 'mpesa', 'credit'):
                d[t.payment_method] += rev
            d['count'] += 1
        if verbose:
            self.stdout.write("  per staff:")
            for name, d in sorted(by_staff.items(), key=lambda kv: -kv[1]['count']):
                self.stdout.write(
                    f"    {name}: {d['count']}txn cash={d['cash']:.0f} "
                    f"mpesa={d['mpesa']:.0f} credit={d['credit']:.0f}"
                )
        else:
            self.stdout.write(f"  {len(by_staff)} staff involved (--verbose for the breakdown)")

        # Any real sale with no way to price it — sale_amount unset AND the
        # item's own selling_price is 0/blank means revenue() silently reads 0.
        broken = [
            t for t in txns
            if t.payment_method in ('cash', 'mpesa', 'credit')
            and t.sale_amount is None
            and (not t.item or not t.item.selling_price)
        ]
        if broken:
            self.stdout.write(self.style.ERROR(
                f"  FLAG: {len(broken)} unpriced transaction(s) — may read as KES 0 revenue."
                + ('' if verbose else ' (--verbose for txn ids)')
            ))
            if verbose:
                for t in broken[:15]:
                    self.stdout.write(f"    txn#{t.id} item_id={t.item_id} qty={t.qty} pm={t.payment_method}")
        else:
            self.stdout.write("  OK — every sale priced correctly.")

    # ── [2] Shifts ───────────────────────────────────────────────────────────
    def _section_shifts(self, business, day_start, day_end, verbose):
        self.stdout.write(self.style.WARNING("[2] SHIFTS"))
        from core.shift_views import _reconcile, _shift_station

        shifts = (
            Shift.objects.filter(business=business)
            .filter(Q(started_at__lte=day_end) & (Q(ended_at__gte=day_start) | Q(ended_at__isnull=True)))
            .select_related('staff__userprofile')
            .order_by('started_at')
        )
        if not shifts.exists():
            self.stdout.write("  none")
            return

        rows = []
        flags = 0
        for s in shifts:
            role = getattr(getattr(s.staff, 'userprofile', None), 'role', '?')
            station = _shift_station(s) or '?'
            rec = _reconcile(s)
            var_txt = ''
            needs_review = False
            if s.closing_cash_counted is not None:
                variance = float(s.closing_cash_counted) - rec['expected_cash']
                needs_review = abs(variance) > 500 and s.variance_review_status not in ('acknowledged', 'flagged')
                flag = ' ⚠️' if needs_review else ''
                var_txt = f" var={variance:.0f}{flag}"
            if needs_review:
                flags += 1
            self.stdout.write(
                f"  #{s.id} {s.staff.get_full_name() or s.staff.username} ({role}) {station} "
                f"{timezone.localtime(s.started_at).strftime('%H:%M')}-"
                f"{timezone.localtime(s.ended_at).strftime('%H:%M') if s.ended_at else 'OPEN'} "
                f"c={rec['cash_sales']:.0f} m={rec['mpesa_sales']:.0f} cr={rec['credit_sales']:.0f}{var_txt}"
            )
            rows.append((s, station, role))

        if flags:
            self.stdout.write(self.style.ERROR(f"  FLAG: {flags} shift(s) with an unreviewed variance >KES 500."))

        # Overlap visibility — informational only (already correctly handled
        # by _shift_active_segments()'s own de-overlap logic); verbose only.
        if verbose:
            for i, (s1, st1, role1) in enumerate(rows):
                if role1 in ('waitress', 'manager', 'owner'):
                    continue
                for s2, st2, role2 in rows[i + 1:]:
                    if role2 in ('waitress', 'manager', 'owner') or st1 != st2:
                        continue
                    e1 = s1.ended_at or timezone.now()
                    e2 = s2.ended_at or timezone.now()
                    if s1.started_at < e2 and s2.started_at < e1:
                        self.stdout.write(
                            f"  (info) #{s1.id}/#{s2.id} overlap on {st1} — already "
                            f"correctly split by segment logic."
                        )

    # ── [3] Stock movement ──────────────────────────────────────────────────
    def _section_stock(self, business, day_start, day_end, verbose):
        self.stdout.write(self.style.WARNING("[3] STOCK MOVEMENT"))
        txns = Transaction.objects.filter(
            business=business, created_at__gte=day_start, created_at__lte=day_end,
        ).exclude(payment_method='void')
        by_type = txns.values('type').annotate(n=Count('id'), qty_sum=Sum('qty')).order_by('type')
        if not by_type:
            self.stdout.write("  none")
            return
        self.stdout.write(
            "  " + " | ".join(f"{row['type']}:{row['n']}(qty {row['qty_sum']})" for row in by_type)
        )

        item_ids = set(txns.values_list('item_id', flat=True)) - {None}
        negative = []
        for item in Item.objects.filter(id__in=item_ids):
            bal = item.current_balance()
            if bal is not None and bal < 0:
                negative.append((item, bal))
        if negative:
            self.stdout.write(self.style.ERROR(
                f"  FLAG: {len(negative)} item(s) show a NEGATIVE balance "
                f"(structurally impossible per capped_deduction()):"
            ))
            for item, bal in negative:
                self.stdout.write(f"    {item.description} (id={item.id}): {bal}")
        else:
            self.stdout.write(f"  OK — {len(item_ids)} item(s) touched, none negative.")

    # ── [4] Stock-take variances ─────────────────────────────────────────────
    def _section_variances(self, business, day_start, day_end, verbose):
        self.stdout.write(self.style.WARNING("[4] STOCK-TAKE VARIANCES"))
        qs = (
            StockVarianceQuery.objects.filter(stock_take__business=business)
            .filter(
                Q(created_at__gte=day_start, created_at__lte=day_end)
                | Q(owner_acted_at__gte=day_start, owner_acted_at__lte=day_end)
            )
            .select_related('item', 'queried_staff__user')
            .distinct()
        )
        if not qs.exists():
            self.stdout.write("  none")
            return
        shown = list(qs) if verbose else list(qs)[:5]
        for v in shown:
            staff_name = v.queried_staff.user.username if v.queried_staff and v.queried_staff.user else '?'
            self.stdout.write(
                f"  #{v.id} {v.item_name_cache} {v.direction} book={v.book_balance} "
                f"actual={v.actual_count} status={v.status} kind={v.kind} staff={staff_name}"
            )
        remaining = qs.count() - len(shown)
        if remaining > 0:
            self.stdout.write(f"  (+{remaining} more — --verbose to see all)")

    # ── [5] Receiving ────────────────────────────────────────────────────────
    def _section_receiving(self, business, day_start, day_end, verbose):
        self.stdout.write(self.style.WARNING("[5] RECEIVING"))
        receipts = KitchenStockReceipt.objects.filter(
            business=business, created_at__gte=day_start, created_at__lte=day_end,
        )
        events = PortioningEvent.objects.filter(
            business=business, created_at__gte=day_start, created_at__lte=day_end,
        )
        receives = KegWeightReading.objects.filter(
            barrel__business=business, reading_type='RECEIVE',
            recorded_at__gte=day_start, recorded_at__lte=day_end,
        ).select_related('barrel__item')
        plain_receipts = Transaction.objects.filter(
            business=business, type='Receipt',
            created_at__gte=day_start, created_at__lte=day_end,
        ).exclude(invoice_no__in=['[ADJ]', '[ADJ-NOLOSS]', '[SVQ]', '[SVQ-REVERT]']).select_related('item')

        total = receipts.count() + events.count() + receives.count() + plain_receipts.count()
        if not total:
            self.stdout.write("  none")
            return
        self.stdout.write(
            f"  {total} event(s): {receipts.count()} KitchenStockReceipt, "
            f"{events.count()} Gawa Kuku, {receives.count()} keg receive, "
            f"{plain_receipts.count()} plain Receipt txn"
        )
        if not verbose:
            self.stdout.write("  (--verbose for the itemized list)")
            return
        for r in receipts:
            self.stdout.write(f"    KitchenStockReceipt#{r.id} status={r.status} {timezone.localtime(r.created_at).strftime('%H:%M')}")
        for e in events:
            self.stdout.write(f"    PortioningEvent#{e.id} {timezone.localtime(e.created_at).strftime('%H:%M')}")
        for kr in receives:
            self.stdout.write(
                f"    Keg receive barrel#{kr.barrel_id} "
                f"({kr.barrel.item.description if kr.barrel and kr.barrel.item else '?'}) "
                f"{kr.weight_kg}kg {timezone.localtime(kr.recorded_at).strftime('%H:%M')}"
            )
        for t in plain_receipts:
            self.stdout.write(
                f"    Receipt txn#{t.id} {t.item.description if t.item else '?'} "
                f"+{-t.qty if t.qty < 0 else t.qty} {timezone.localtime(t.created_at).strftime('%H:%M')}"
            )

    # ── [6] Expenses ─────────────────────────────────────────────────────────
    def _section_expenses(self, business, day_start, day_end, verbose):
        self.stdout.write(self.style.WARNING("[6] EXPENSES"))
        sel_date = day_start.date()
        petty = PettyCash.objects.filter(business=business, date=sel_date)
        if petty.exists():
            approved = petty.filter(status='approved').aggregate(t=Sum('amount'))['t'] or 0
            pending = petty.filter(status='pending').aggregate(t=Sum('amount'))['t'] or 0
            rejected = petty.filter(status='rejected').aggregate(t=Sum('amount'))['t'] or 0
            self.stdout.write(
                f"  Counter Cash: approved={approved} (reduces till) pending={pending} "
                f"(not yet) rejected={rejected} (never)"
            )
            if verbose:
                for p in petty.select_related('recorded_by'):
                    self.stdout.write(
                        f"    #{p.id} {p.get_reason_display()} {p.amount} {p.status} "
                        f"by={p.recorded_by.username if p.recorded_by else '?'} station={p.station or '(unset)'}"
                    )
        else:
            self.stdout.write("  Counter Cash: none")

        expenses = BusinessExpense.objects.filter(business=business, date=sel_date)
        if expenses.exists():
            total = expenses.aggregate(t=Sum('amount'))['t'] or 0
            self.stdout.write(f"  Matumizi (bookkeeping-only, never reduces till): {total}")
            if verbose:
                for e in expenses.select_related('recorded_by'):
                    self.stdout.write(
                        f"    #{e.id} {e.get_category_display()} {e.amount} station={e.station or '(unset)'} "
                        f"by={e.recorded_by.username if e.recorded_by else '?'}"
                    )
        else:
            self.stdout.write("  Matumizi: none")

    # ── [7] Corrections ──────────────────────────────────────────────────────
    def _section_corrections(self, business, day_start, day_end, verbose):
        self.stdout.write(self.style.WARNING("[7] CORRECTIONS (visibility only)"))
        base = Transaction.objects.filter(
            business=business, created_at__gte=day_start, created_at__lte=day_end,
        )
        voided = base.filter(payment_method='void').count()
        split_children = base.filter(split_from__isnull=False).count()
        reverted = base.filter(invoice_no__startswith='[SVQ-REVERT]').count()
        adjustments = base.filter(invoice_no__in=['[ADJ]', '[ADJ-NOLOSS]']).count()
        theft = base.filter(invoice_no__icontains='[THEFT]').count()
        self.stdout.write(
            f"  voided={voided} split={split_children} reverted={reverted} "
            f"adjusted={adjustments} theft_tagged={theft}"
        )
