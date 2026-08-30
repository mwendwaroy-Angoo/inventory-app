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


class Command(BaseCommand):
    help = (
        "READ-ONLY. One-shot consolidated audit of every transactional/service "
        "process for one business on one day — cash/mpesa/credit sales tie-out "
        "(business-wide, per-station, per-staff), per-shift reconciliation with "
        "an overlap check, stock movement type breakdown + a negative-balance "
        "integrity check, stock-take variances raised or resolved that day, "
        "receiving events (Kitchen Stock Receipts, keg receives, Gawa Kuku "
        "portioning), expenses (Counter Cash + Matumizi), and a visibility list "
        "of corrections (voids, splits, reverts, Rekebisha, theft verdicts) — "
        "then orchestrates the existing diagnose_recent_sales_visibility / "
        "audit_debt_ledger_integrity / audit_money_path_integrity commands for "
        "the deeper structural checks those already cover, so this is one "
        "single report to run rather than six separate ones. Changes NOTHING.\n\n"
        "2026-08-30 live request (Roy, Monsoon Inn): 'are you able to audit all "
        "transactional and service processes for monsoon for me from yesterday' "
        "— this session has no direct access to the live production database, "
        "so this command is the concrete deliverable: run it yourself via "
        "Render's Shell tab and read the output.\n\n"
        "Usage: python manage.py audit_daily_operations --business=\"Monsoon Inn\" "
        "[--date=YYYY-MM-DD, default: yesterday]"
    )

    def add_arguments(self, parser):
        parser.add_argument('--business', type=str, required=True, help='Business name substring.')
        parser.add_argument('--date', type=str, help='YYYY-MM-DD, local calendar day. Default: yesterday.')

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

        for business in businesses:
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"\n{'=' * 74}\n[{business.name}] — DAILY OPERATIONS AUDIT for "
                f"{sel_date.isoformat()}\n{'=' * 74}"
            ))

            self._section_sales(business, day_start, day_end)
            self._section_shifts(business, day_start, day_end)
            self._section_stock(business, day_start, day_end)
            self._section_variances(business, day_start, day_end)
            self._section_receiving(business, day_start, day_end)
            self._section_expenses(business, day_start, day_end)
            self._section_corrections(business, day_start, day_end)

            self.stdout.write(self.style.MIGRATE_HEADING(
                f"\n--- Orchestrated deeper structural checks for [{business.name}] "
                f"(whole ledger, not date-scoped — a real issue may predate today) ---"
            ))
            call_command(
                'diagnose_recent_sales_visibility',
                business=business.name, date=sel_date.isoformat(), stdout=self.stdout,
            )
            call_command(
                'audit_debt_ledger_integrity',
                business=business.name, all_customers=True, stdout=self.stdout,
            )
            call_command('audit_money_path_integrity', business=business.name, stdout=self.stdout)

            self.stdout.write(self.style.SUCCESS(
                f"\n=== [{business.name}] audit complete for {sel_date.isoformat()} ===\n"
            ))

    # ── [1] Sales ────────────────────────────────────────────────────────────
    def _section_sales(self, business, day_start, day_end):
        self.stdout.write(self.style.WARNING("\n[1] SALES — cash / mpesa / credit tie-out"))
        txns = list(
            Transaction.objects.filter(
                business=business, type='Issue',
                created_at__gte=day_start, created_at__lte=day_end,
            )
            .exclude(payment_method='void')
            .select_related('item__store', 'recorded_by')
        )
        if not txns:
            self.stdout.write("  No sales recorded this day.")
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

        self.stdout.write(f"  Bar:     cash={bar_cash:.2f}  mpesa={bar_mpesa:.2f}  credit={bar_credit:.2f}")
        self.stdout.write(f"  Kitchen: cash={kit_cash:.2f}  mpesa={kit_mpesa:.2f}  credit={kit_credit:.2f}")
        self.stdout.write(
            f"  TOTAL:   cash={total_cash:.2f}  mpesa={total_mpesa:.2f}  credit={total_credit:.2f}  "
            f"confirmed(cash+mpesa)={total_cash + total_mpesa:.2f}  "
            f"grand_total={total_cash + total_mpesa + total_credit:.2f}"
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
        self.stdout.write("  Per staff (recorded_by):")
        for name, d in sorted(by_staff.items(), key=lambda kv: -kv[1]['count']):
            self.stdout.write(
                f"    {name}: {d['count']} txn(s) — cash={d['cash']:.2f} "
                f"mpesa={d['mpesa']:.2f} credit={d['credit']:.2f}"
            )

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
                f"  FLAG: {len(broken)} transaction(s) with no sale_amount AND no "
                f"item.selling_price to price from — may be reading as KES 0 revenue "
                f"for a real sale:"
            ))
            for t in broken[:15]:
                self.stdout.write(f"    txn#{t.id} item_id={t.item_id} qty={t.qty} pm={t.payment_method}")
        else:
            self.stdout.write("  OK — every sale this day has a real price to compute revenue from.")

    # ── [2] Shifts ───────────────────────────────────────────────────────────
    def _section_shifts(self, business, day_start, day_end):
        self.stdout.write(self.style.WARNING("\n[2] SHIFTS — per-shift reconciliation + overlap visibility"))
        from core.shift_views import _reconcile, _shift_station

        shifts = (
            Shift.objects.filter(business=business)
            .filter(Q(started_at__lte=day_end) & (Q(ended_at__gte=day_start) | Q(ended_at__isnull=True)))
            .select_related('staff__userprofile')
            .order_by('started_at')
        )
        if not shifts.exists():
            self.stdout.write("  No shift active during this day.")
            return

        rows = []
        for s in shifts:
            role = getattr(getattr(s.staff, 'userprofile', None), 'role', '?')
            station = _shift_station(s) or '?'
            rec = _reconcile(s)
            var_txt = ''
            if s.closing_cash_counted is not None:
                variance = float(s.closing_cash_counted) - rec['expected_cash']
                needs_review = abs(variance) > 500 and s.variance_review_status not in ('acknowledged', 'flagged')
                flag = ' ⚠️ unreviewed variance >KES 500' if needs_review else ''
                var_txt = (
                    f" | closed={s.closing_cash_counted} expected={rec['expected_cash']:.2f} "
                    f"variance={variance:.2f}{flag}"
                )
            self.stdout.write(
                f"  shift#{s.id} {s.staff.get_full_name() or s.staff.username} ({role}) "
                f"{station} {timezone.localtime(s.started_at).strftime('%H:%M')}→"
                f"{timezone.localtime(s.ended_at).strftime('%H:%M') if s.ended_at else 'OPEN'} "
                f"cash={rec['cash_sales']:.2f} mpesa={rec['mpesa_sales']:.2f} "
                f"credit={rec['credit_sales']:.2f}{var_txt}"
            )
            rows.append((s, station, role))

        # Overlap visibility — informational only. _shift_active_segments()
        # already de-overlaps two real-custodian shifts on the same station
        # (the newer one caps the older one's window) so the cash/mpesa
        # figures above are already correct even when this fires; shown so
        # the sequence of who-handed-off-to-whom is visible, not as an alarm.
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
                        f"  (info) shift#{s1.id} and shift#{s2.id} overlap on {st1} — "
                        f"already correctly split by the app's own segment logic, "
                        f"shown for visibility only."
                    )

    # ── [3] Stock movement ──────────────────────────────────────────────────
    def _section_stock(self, business, day_start, day_end):
        self.stdout.write(self.style.WARNING("\n[3] STOCK MOVEMENT — deduction/increment integrity"))
        txns = Transaction.objects.filter(
            business=business, created_at__gte=day_start, created_at__lte=day_end,
        ).exclude(payment_method='void')
        by_type = txns.values('type').annotate(n=Count('id'), qty_sum=Sum('qty')).order_by('type')
        if not by_type:
            self.stdout.write("  No stock movement this day.")
            return
        for row in by_type:
            self.stdout.write(f"  {row['type']}: {row['n']} txn(s), net qty change {row['qty_sum']}")

        item_ids = set(txns.values_list('item_id', flat=True)) - {None}
        negative = []
        for item in Item.objects.filter(id__in=item_ids):
            bal = item.current_balance()
            if bal is not None and bal < 0:
                negative.append((item, bal))
        if negative:
            self.stdout.write(self.style.ERROR(
                f"  FLAG: {len(negative)} item(s) touched today show a NEGATIVE "
                f"current balance (should be structurally impossible per "
                f"Item.capped_deduction()):"
            ))
            for item, bal in negative:
                self.stdout.write(f"    {item.description} (id={item.id}): balance={bal}")
        else:
            self.stdout.write(f"  OK — none of the {len(item_ids)} item(s) touched today show a negative balance.")

    # ── [4] Stock-take variances ─────────────────────────────────────────────
    def _section_variances(self, business, day_start, day_end):
        self.stdout.write(self.style.WARNING("\n[4] STOCK-TAKE VARIANCES raised or resolved today"))
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
            self.stdout.write("  None.")
            return
        for v in qs:
            staff_name = v.queried_staff.user.username if v.queried_staff and v.queried_staff.user else '?'
            self.stdout.write(
                f"  variance#{v.id} item={v.item_name_cache} {v.direction} "
                f"book={v.book_balance} actual={v.actual_count} status={v.status} "
                f"kind={v.kind} staff={staff_name}"
            )

    # ── [5] Receiving ────────────────────────────────────────────────────────
    def _section_receiving(self, business, day_start, day_end):
        self.stdout.write(self.style.WARNING("\n[5] RECEIVING — stock arriving into the business today"))
        found = False

        receipts = KitchenStockReceipt.objects.filter(
            business=business, created_at__gte=day_start, created_at__lte=day_end,
        )
        for r in receipts:
            found = True
            self.stdout.write(
                f"  KitchenStockReceipt#{r.id} status={r.status} "
                f"created={timezone.localtime(r.created_at).strftime('%H:%M')}"
            )

        events = PortioningEvent.objects.filter(
            business=business, created_at__gte=day_start, created_at__lte=day_end,
        )
        for e in events:
            found = True
            self.stdout.write(
                f"  PortioningEvent#{e.id} created={timezone.localtime(e.created_at).strftime('%H:%M')}"
            )

        receives = KegWeightReading.objects.filter(
            barrel__business=business, reading_type='RECEIVE',
            recorded_at__gte=day_start, recorded_at__lte=day_end,
        ).select_related('barrel__item')
        for kr in receives:
            found = True
            self.stdout.write(
                f"  Keg receive: barrel#{kr.barrel_id} ({kr.barrel.item.description if kr.barrel and kr.barrel.item else '?'}) "
                f"{kr.weight_kg}kg at {timezone.localtime(kr.recorded_at).strftime('%H:%M')}"
            )

        plain_receipts = Transaction.objects.filter(
            business=business, type='Receipt',
            created_at__gte=day_start, created_at__lte=day_end,
        ).exclude(invoice_no__in=['[ADJ]', '[ADJ-NOLOSS]', '[SVQ]', '[SVQ-REVERT]']).select_related('item')
        for t in plain_receipts:
            found = True
            self.stdout.write(
                f"  Receipt txn#{t.id} {t.item.description if t.item else '?'} +{-t.qty if t.qty < 0 else t.qty} "
                f"at {timezone.localtime(t.created_at).strftime('%H:%M')}"
            )

        if not found:
            self.stdout.write("  No receiving events recorded this day.")

    # ── [6] Expenses ─────────────────────────────────────────────────────────
    def _section_expenses(self, business, day_start, day_end):
        self.stdout.write(self.style.WARNING("\n[6] EXPENSES — Counter Cash (Petty Cash) + Matumizi (ad-hoc)"))
        sel_date = day_start.date()
        petty = PettyCash.objects.filter(business=business, date=sel_date)
        if petty.exists():
            approved = petty.filter(status='approved').aggregate(t=Sum('amount'))['t'] or 0
            pending = petty.filter(status='pending').aggregate(t=Sum('amount'))['t'] or 0
            rejected = petty.filter(status='rejected').aggregate(t=Sum('amount'))['t'] or 0
            self.stdout.write(
                f"  Counter Cash: approved=KES {approved} (already reduces till_expected_cash) | "
                f"pending=KES {pending} (NOT yet reduced) | rejected=KES {rejected} (never reduces)"
            )
            for p in petty.select_related('recorded_by'):
                self.stdout.write(
                    f"    #{p.id} {p.get_reason_display()} KES {p.amount} status={p.status} "
                    f"by={p.recorded_by.username if p.recorded_by else '?'} station={p.station or '(unset)'}"
                )
        else:
            self.stdout.write("  Counter Cash: none recorded this day.")

        expenses = BusinessExpense.objects.filter(business=business, date=sel_date)
        if expenses.exists():
            total = expenses.aggregate(t=Sum('amount'))['t'] or 0
            self.stdout.write(f"  Matumizi (ad-hoc, bookkeeping-only — never reduces till_expected_cash): KES {total}")
            for e in expenses.select_related('recorded_by'):
                self.stdout.write(
                    f"    #{e.id} {e.get_category_display()} KES {e.amount} station={e.station or '(unset)'} "
                    f"by={e.recorded_by.username if e.recorded_by else '?'}"
                )
        else:
            self.stdout.write("  Matumizi: none recorded this day.")

    # ── [7] Corrections ──────────────────────────────────────────────────────
    def _section_corrections(self, business, day_start, day_end):
        self.stdout.write(self.style.WARNING(
            "\n[7] CORRECTIONS — voids, splits, reverts, Rekebisha, theft "
            "verdicts (visibility only)"
        ))
        base = Transaction.objects.filter(
            business=business, created_at__gte=day_start, created_at__lte=day_end,
        )
        voided = base.filter(payment_method='void').count()
        split_children = base.filter(split_from__isnull=False).count()
        reverted = base.filter(invoice_no__startswith='[SVQ-REVERT]').count()
        adjustments = base.filter(invoice_no__in=['[ADJ]', '[ADJ-NOLOSS]']).count()
        theft = base.filter(invoice_no__icontains='[THEFT]').count()
        self.stdout.write(
            f"  Voided: {voided} | Split fragments: {split_children} | "
            f"Miscount reverts: {reverted} | Rekebisha adjustments: {adjustments} | "
            f"Theft-tagged corrections: {theft}"
        )
