import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Business
from core.models import BarTabEntry, Receipt, Transaction


class Command(BaseCommand):
    help = (
        "READ-ONLY. 2026-08-21 urgent live report (Roy, Monsoon Inn): the "
        "'\U0001F550 Malipo ya Hivi Karibuni' (Recent Payments) panel on Kitchen "
        "Board/Bar Board/Quick Sell — powered by recent_settled_tabs_api — showed "
        "NOTHING for today even though the Receipts list clearly showed real "
        "entries for today. Reproduced the exact same query (owner + staff, "
        "portion-item + KitchenBatch sale, explicit ?date=&station=kitchen) "
        "against a clean test database and could not find a bug — every scenario "
        "tested returned the sale correctly. This command runs the SAME query "
        "logic that endpoint uses against REAL production data for one day, and "
        "cross-references it against every Receipt issued that day, printing "
        "exactly which category each Receipt's underlying transaction(s) fall "
        "into (found in the direct list / found in the tab list / EXCLUDED, with "
        "the specific reason) — so the real root cause can finally be pinned "
        "down instead of guessed at. Changes NOTHING.\n\n"
        "Usage: python manage.py diagnose_recent_sales_visibility "
        "--business=\"Monsoon Inn\" [--date=YYYY-MM-DD] [--station=kitchen|bar]"
    )

    def add_arguments(self, parser):
        parser.add_argument('--business', type=str, required=True, help='Business name substring.')
        parser.add_argument('--date', type=str, help='YYYY-MM-DD, local calendar day. Default: today.')
        parser.add_argument('--station', type=str, choices=['bar', 'kitchen'], help='Restrict to one station.')

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
            sel_date = timezone.localdate()

        day_start = timezone.make_aware(datetime.datetime.combine(sel_date, datetime.time.min))
        day_end = timezone.make_aware(datetime.datetime.combine(sel_date, datetime.time.max))
        station_filter = options.get('station')

        for business in businesses:
            self.stdout.write(self.style.WARNING(
                f"\n=== [{business.name}] {sel_date.isoformat()} "
                f"({timezone.localtime(day_start)} → {timezone.localtime(day_end)}, "
                f"server TIME_ZONE-aware) ==="
            ))

            # ── EXACTLY recent_settled_tabs_api's own two queries ──────────
            direct_qs = (
                Transaction.objects.filter(
                    business=business, type='Issue',
                    payment_method__in=['cash', 'mpesa', 'credit'],
                    created_at__gte=day_start, created_at__lte=day_end,
                    tab_entry__isnull=True,
                ).select_related('item__store')
            )
            direct_by_id = {}
            for t in direct_qs:
                is_kitchen = bool(t.item and t.item.store and t.item.store.is_kitchen)
                station = 'kitchen' if is_kitchen else 'bar'
                shown = (not station_filter) or (station_filter == station)
                direct_by_id[t.id] = (station, shown)

            paid_entries_qs = BarTabEntry.objects.filter(
                tab__business=business, is_paid=True,
                paid_at__gte=day_start, paid_at__lte=day_end,
            ).exclude(payment_method='void').select_related('transaction__item__store', 'tab')
            tab_by_txn_id = {}
            for e in paid_entries_qs:
                if not e.transaction_id:
                    continue
                is_kitchen = bool(e.transaction.item and e.transaction.item.store and e.transaction.item.store.is_kitchen)
                station = 'kitchen' if is_kitchen else 'bar'
                shown = (not station_filter) or (station_filter == station)
                tab_by_txn_id[e.transaction_id] = (station, shown, e.tab.source)

            self.stdout.write(
                f"direct_txns query matched {len(direct_by_id)} transaction(s); "
                f"paid tab entries query matched {len(tab_by_txn_id)} entrie(s)."
            )

            # ── Cross-reference against every Receipt issued that day ──────
            receipts = Receipt.objects.filter(
                business=business, created_at__gte=day_start, created_at__lte=day_end,
            ).order_by('created_at')
            self.stdout.write(f"\n-- {receipts.count()} Receipt(s) issued this day --")
            for r in receipts:
                lines = r.lines or []
                txn_ids = [l.get('txn_id') for l in lines if isinstance(l, dict) and l.get('txn_id')]
                self.stdout.write(
                    f"  receipt#{r.receipt_number} (id={r.id}) {timezone.localtime(r.created_at)} "
                    f"source={r.source!r} payment_method={r.payment_method!r} "
                    f"customer={r.customer_name!r} total=KES {r.total} lines_with_txn_id={len(txn_ids)}/{len(lines)}"
                )
                if not txn_ids:
                    self.stdout.write(self.style.WARNING(
                        "    (no txn_id on any line — pre-2026-08-21 receipt, or every "
                        "line is tab-linked/void; can't cross-reference this one)"
                    ))
                    continue
                for tid in txn_ids:
                    try:
                        t = Transaction.objects.select_related('item__store').get(id=tid)
                    except Transaction.DoesNotExist:
                        self.stdout.write(self.style.ERROR(f"    txn#{tid}: DOES NOT EXIST"))
                        continue
                    if tid in direct_by_id:
                        station, shown = direct_by_id[tid]
                        tag = 'SHOWN' if shown else f'EXCLUDED by ?station= filter (is {station})'
                        self.stdout.write(f"    txn#{tid}: in DIRECT list — {tag}")
                    elif tid in tab_by_txn_id:
                        station, shown, source = tab_by_txn_id[tid]
                        tag = 'SHOWN' if shown else f'EXCLUDED by ?station= filter (is {station})'
                        self.stdout.write(f"    txn#{tid}: in TAB list (tab.source={source!r}) — {tag}")
                    else:
                        # Not in either query result — figure out exactly why.
                        reasons = []
                        if t.type != 'Issue':
                            reasons.append(f"type={t.type!r} (must be Issue)")
                        if t.payment_method not in ('cash', 'mpesa', 'credit'):
                            reasons.append(f"payment_method={t.payment_method!r} (must be cash/mpesa/credit)")
                        created_local = timezone.localtime(t.created_at)
                        if not (day_start <= t.created_at <= day_end):
                            reasons.append(
                                f"created_at={created_local} falls OUTSIDE this day's window "
                                f"({timezone.localtime(day_start)} → {timezone.localtime(day_end)}) "
                                f"even though the RECEIPT was issued inside it"
                            )
                        has_tab_entry = False
                        try:
                            has_tab_entry = t.tab_entry is not None
                        except Exception:
                            has_tab_entry = False
                        if has_tab_entry:
                            entry = t.tab_entry
                            if not entry.is_paid:
                                reasons.append(
                                    f"tab_entry#{entry.id} exists but is_paid=False "
                                    f"(still on an open tab, correctly not \"recent payment\" yet)"
                                )
                            elif entry.paid_at is None or not (day_start <= entry.paid_at <= day_end):
                                paid_local = timezone.localtime(entry.paid_at) if entry.paid_at else None
                                reasons.append(
                                    f"tab_entry#{entry.id} is_paid=True but paid_at={paid_local} "
                                    f"falls outside this day's window (item was ORDERED today "
                                    f"but SETTLED on a different day/never)"
                                )
                            elif entry.payment_method == 'void':
                                reasons.append(f"tab_entry#{entry.id} payment_method='void' (excluded)")
                        if not reasons:
                            reasons.append("UNEXPLAINED — every checked condition passed; needs a closer look")
                        self.stdout.write(self.style.ERROR(
                            f"    txn#{tid}: NOT FOUND in either query. Reason(s): " + '; '.join(reasons)
                        ))
