import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt

from .models import Receipt
from .notifications import normalize_ke_phone, send_email_notification, send_email_notification_async, send_sms_notification, send_sms_notification_async
from .views import get_user_profile, owner_required

logger = logging.getLogger(__name__)


@login_required
def receipts_list(request):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect('home')

    from django.utils import timezone as _tz
    now = _tz.localtime(_tz.now())

    try:
        month = int(request.GET.get('month', now.month))
        year  = int(request.GET.get('year',  now.year))
    except (ValueError, TypeError):
        month, year = now.month, now.year

    month = max(1, min(12, month))
    year  = max(2020, min(now.year + 1, year))

    # ?date=YYYY-MM-DD (2026-07-25 live request): a single-day filter above
    # the month/year select, so "how many receipts on the 25th?" is one pick
    # instead of scanning a whole month. Takes priority over month/year when
    # given; falls back to month/year unchanged when absent.
    import datetime as _dt
    sel_date = None
    date_str = (request.GET.get('date') or '').strip()
    if date_str:
        try:
            sel_date = _dt.datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            sel_date = None

    search = request.GET.get('q', '').strip()

    if sel_date:
        qs = Receipt.objects.filter(
            business=user_profile.business,
            created_at__date=sel_date,
        ).select_related('created_by')
    else:
        qs = Receipt.objects.filter(
            business=user_profile.business,
            created_at__year=year,
            created_at__month=month,
        ).select_related('created_by')

    if search:
        qs = qs.filter(customer_name__icontains=search)

    all_receipts = list(qs.order_by('-created_at'))

    # Distinguish an actually-completed sale from a still-live, unpaid tab
    # (2026-07-25 live report: a receipt for an open tab was issued with
    # payment_method='tab' — a plain string matching neither 'cash', 'mpesa'
    # nor 'credit' — which the badge below used to silently render as "Cash",
    # so staff/owner saw an in-progress, unpaid tab looking exactly like a
    # completed cash sale). Batch-resolve every referenced tab's status in
    # ONE query instead of N — same _receipt_all_tab_ids() helper the public
    # receipt page already uses as the single source of truth for "does this
    # receipt still have a live tab".
    from .models import BarTab as _BarTab
    all_tab_ids = set()
    for r in all_receipts:
        all_tab_ids.update(_receipt_all_tab_ids(r))
    _tabs_by_id = (
        {t.id: t for t in _BarTab.objects.filter(id__in=all_tab_ids)}
        if all_tab_ids else {}
    )
    open_tab_ids = {tid for tid, t in _tabs_by_id.items() if t.status == 'OPEN'}

    # Station Scoping Principle — 2026-08-05 live report (Roy): bar staff
    # were still seeing kitchen receipts and vice versa, despite the
    # 2026-07-30 fix below (kept, unchanged: Receipt.source is 'kitchen' for
    # kitchen sales, '' for bar/quick-sell). Root cause of the REMAINING
    # leak: resolve_master_receipt() (core/tab_receipts.py) deliberately
    # consolidates a customer's bar AND kitchen orders into ONE shared
    # receipt/PIN via meta.linked_tab_ids — a real, wanted feature (one bill
    # for the customer) — but a receipt's own `source` field only ever
    # reflects where it was FIRST issued, never that it later absorbed the
    # other counter's items too. A bar-only staffer viewing a bar-sourced
    # receipt that later had a kitchen order merged into it saw kitchen line
    # items and kitchen revenue inside what looked like "their" receipt with
    # zero explanation — and a kitchen-only staffer never saw that same
    # receipt at all (excluded by source), so their own kitchen sale was
    # invisible to them on their own Receipts page. Fixed by basing
    # visibility on which station(s) a receipt ACTUALLY TOUCHES (its own
    # source, plus every linked tab's own source) rather than source alone —
    # closing both directions — and flagging is_mixed_station so a viewer
    # who does see a cross-counter receipt gets a clear "🔀 Mchanganyiko"
    # explanation instead of silent confusion.
    def _receipt_touches(r, want_kitchen):
        own_is_kitchen = (r.source == 'kitchen')
        if own_is_kitchen == want_kitchen:
            return True
        for tid in _receipt_all_tab_ids(r):
            t = _tabs_by_id.get(tid)
            if t is None:
                continue
            if (t.source == 'kitchen') == want_kitchen:
                return True
        return False

    from .views import _station_scope
    _show_bar, _show_kitchen = _station_scope(user_profile)
    for r in all_receipts:
        r.touches_bar     = _receipt_touches(r, False)
        r.touches_kitchen = _receipt_touches(r, True)
        r.is_mixed_station = r.touches_bar and r.touches_kitchen

    if not user_profile.is_owner_or_manager:
        if _show_kitchen and not _show_bar:
            all_receipts = [r for r in all_receipts if r.touches_kitchen]
        elif _show_bar and not _show_kitchen:
            all_receipts = [r for r in all_receipts if r.touches_bar]

    status_filter = request.GET.get('status', 'sold')  # 'sold' (default) | 'all' | 'open'
    receipts = []
    open_tab_count = 0
    for r in all_receipts:
        r.is_open_tab = bool(set(_receipt_all_tab_ids(r)) & open_tab_ids)
        if r.is_open_tab:
            open_tab_count += 1
        if status_filter == 'sold' and r.is_open_tab:
            continue
        if status_filter == 'open' and not r.is_open_tab:
            continue
        receipts.append(r)

    # Build month options for the filter UI (current year, plus one back)
    import calendar as _cal
    month_options = [(m, _cal.month_abbr[m]) for m in range(1, 13)]

    return render(request, 'core/receipts_list.html', {
        'receipts':        receipts,
        'sel_month':       month,
        'sel_year':        year,
        'sel_date':        sel_date.isoformat() if sel_date else '',
        'search':          search,
        'month_options':   month_options,
        'cur_year':        now.year,
        'status_filter':   status_filter,
        'open_tab_count':  open_tab_count,
        'total_count':     len(all_receipts),
        'receipt_count':   len(receipts),
    })


def _receipt_all_tab_ids(receipt):
    """Every BarTab id this receipt's live state should be computed from — its
    own meta.tab_id (if any) plus every tab in meta.linked_tab_ids.

    A receipt can be a customer's "master" bill via EITHER slot:
    resolve_master_receipt() (core/tab_receipts.py) links a tab into a
    receipt's linked_tab_ids (Priority 2/3/4) even when that receipt has no
    tab_id of its own — e.g. an earlier Deni/credit receipt with no live tab,
    or (via the same-day/same-name consolidation in Priority 4) a completely
    unrelated, already-completed one-off cash/mpesa sale. Root cause of a
    real production report: code that only checked meta.tab_id treated such a
    receipt as "not live" and fell back to its stale original snapshot — so a
    customer scanning the wall QR and entering their PIN landed on an old,
    already-resolved receipt showing their brand-new tab as if it were
    already paid. Always use this helper instead of reading meta.tab_id
    directly — every function below (and receipt_pay in this same file) was
    audited and fixed to do so.
    """
    if not receipt.meta:
        return []
    tab_id = receipt.meta.get('tab_id')
    linked = list(receipt.meta.get('linked_tab_ids') or [])
    return ([tab_id] if tab_id else []) + linked


def _receipt_tab_pin(receipt):
    """The customer-facing PIN(s) of this receipt's linked tab(s), for
    display on the customer's own receipt page.

    2026-08-09 live request (Roy) — a customer whose tab was already fully
    paid off had no way to find that receipt again by name (name search on
    the public wall-QR page is deliberately restricted to still-open/still-
    owing tabs, to stop anyone typing a guessable name from browsing a
    customer's ENTIRE payment history with no login — see the security
    revert this same day in core/keg_views.py). PIN search doesn't have
    that problem — it's already the private, per-tab credential this app
    uses everywhere else — so it was widened to reach a customer's full
    history (core.keg_views._findable_tabs_qs_for_pin). But the PIN was
    NEVER shown to the customer themselves, only to staff in the tabs
    drawer — so widening the search alone didn't actually help until the
    customer has some way to know/keep their own PIN. This surfaces it
    here, on their own receipt, so it can be noted down for later.
    """
    from .models import BarTab as _BarTab
    ids = _receipt_all_tab_ids(receipt)
    if not ids:
        return []
    return list(
        _BarTab.objects.filter(id__in=ids, business=receipt.business)
        .exclude(tab_pin='')
        .values_list('tab_pin', flat=True)
        .distinct()
    )


def _live_direct_lines(receipt):
    """2026-08-21 live report (Roy): "even if an item is deleted from
    recent sales in the food tab side or tabs side, the receipt still
    shows the item when it should not." Tab-linked receipts already
    self-heal via _get_live_tab_state (it excludes payment_method='void'
    entries live, every request). A DIRECT (non-tab) sale's receipt had no
    equivalent — receipt.lines is a static JSONField snapshot with no
    reference back to the Transaction that created each line, so a later
    correction on that sale via the "Recent Payments" panel could never be
    reflected on an already-issued receipt.

    Every checkout call site (Quick Sell, Bar Board's keg-cart checkout,
    Kitchen Board) now stamps 'txn_id' on each line it writes, going
    forward. Two corrections are handled here, live, at render time:

    1. void_direct_transaction (🗑 Futa, sets qty=0/payment_method='void')
       — the line is dropped entirely.
    2. split_transaction_payment_method (✂️ Gawanya) — same-day follow-up
       (Roy: "will backdating, gawanya and all other relevant functions...
       change and adjust the receipt"). The split-off sibling never gets
       its own receipt line (the receipt may already be issued by the
       time a Gawanya correction happens) — Transaction.split_from links
       it back to the line's own transaction, so it's synthesized here as
       an additional line (same name/qty as the parent — it's genuinely
       the same item, just a different payment channel for part of it),
       and the parent's own now-reduced sale_amount is read live instead
       of the stale static subtotal. Only one level deep — see
       Transaction.split_from's own docstring for why that's an accepted
       limitation, not a bug.

    correct_transaction_date (📅 Tarehe) needs no handling here — it never
    changes what a line shows, only when it counts for revenue purposes.
    correct_transaction_preset already has its own dedicated, existing
    in-place receipt-line rename (2026-08-01), so it's not duplicated here.

    Deliberately does NOT recompute an UNTOUCHED line's subtotal/name live
    — avoiding the exact kind of live-recompute precision trap this app
    has hit before (Kuku/Chipo Mapato) for the vast majority of lines that
    were never corrected at all. Returns None (meaning: nothing to
    change, use receipt.lines exactly as before) whenever recomputing
    isn't possible — a receipt issued before this fix has no txn_id on
    any line — or whenever nothing was actually voided or split.
    """
    lines = receipt.lines or []
    if not lines or not all(isinstance(l, dict) and l.get('txn_id') for l in lines):
        return None
    from .models import Transaction as _Txn
    txn_ids = [l['txn_id'] for l in lines]
    txns = {
        t.id: t for t in
        _Txn.objects.filter(id__in=txn_ids, business_id=receipt.business_id)
    }
    children_by_parent = {}
    for c in _Txn.objects.filter(split_from_id__in=txn_ids, business_id=receipt.business_id):
        children_by_parent.setdefault(c.split_from_id, []).append(c)

    changed = bool(children_by_parent)
    live_lines = []
    for l in lines:
        t = txns.get(l['txn_id'])
        if t is None or t.payment_method == 'void' or not t.qty:
            changed = True
            continue
        if l['txn_id'] in children_by_parent:
            new_l = dict(l)
            new_l['subtotal'] = float(t.revenue())
            live_lines.append(new_l)
        else:
            live_lines.append(l)
        for child in children_by_parent.get(l['txn_id'], []):
            live_lines.append({
                'name': l.get('name', ''),
                'qty': l.get('qty', 1),
                'subtotal': float(child.revenue()),
                'txn_id': child.id,
            })

    if not changed:
        return None
    return live_lines


def _get_live_tab_state(receipt):
    """Return (is_live, tab_status, lines, outstanding) for a tab-linked receipt.

    Returns ALL entries (paid and unpaid) so the customer can see what they've
    already paid (✓) and what is still pending (checkbox). Each line carries:
        entry_id  — set for unpaid entries, None for paid (pay section filters by this)
        is_paid   — bool flag
        is_kitchen — bool, for station icon rendering
    The returned ``outstanding`` total is the sum of UNPAID entries only.
    Returns is_live=False when this receipt has no tab reference at all (see
    _receipt_all_tab_ids) or none of them resolve to a real tab.
    """
    all_tab_ids = _receipt_all_tab_ids(receipt)
    if not all_tab_ids:
        return False, None, None, None
    try:
        from .models import BarTab as _BarTab
        lines = []
        outstanding = 0.0
        tabs_found = []
        for btab_id in all_tab_ids:
            try:
                btab = _BarTab.objects.get(id=btab_id, business=receipt.business)
            except _BarTab.DoesNotExist:
                continue
            tabs_found.append(btab)
            for e in btab.entries.all().select_related(
                'transaction__item__store'
            ).order_by('id'):
                # Entries removed via ✕ (payment_method='void', is_paid=True) are
                # excluded entirely — they were data-entry corrections, not real sales.
                if e.payment_method == 'void':
                    continue
                is_kitchen = False
                try:
                    is_kitchen = e.transaction.item.store.is_kitchen
                except Exception:
                    pass
                icon = '🍽 ' if is_kitchen else '🍺 '
                amt = float(e.amount)
                if not e.is_paid:
                    # 2026-08-24 live report (Roy, Marley's tab): "still
                    # owed" must be remaining_amount(), not the line's full
                    # price — a full-item transfer can land an entry here
                    # already carrying a real amount_paid (collected
                    # earlier via the debt tracker), and Jumla is this
                    # app's own established "what's still unpaid right now"
                    # figure (see the 2026-08-01 "Umeshalipa Hadi Sasa"
                    # entry), never the gross original total. The line
                    # subtotal itself stays the true full item price —
                    # only the OUTSTANDING total is adjusted.
                    outstanding += float(e.remaining_amount())
                lines.append({
                    'name': icon + e.description,
                    'qty': 1,
                    'subtotal': amt,
                    'entry_id': e.id if not e.is_paid else None,
                    'tab_id': btab_id,
                    'is_paid': e.is_paid,
                    'is_kitchen': is_kitchen,
                })

        if not tabs_found:
            return False, None, None, None

        # Effective status across every linked tab: any tab still OPEN wins
        # (the bill is live); otherwise settled-with-outstanding is DEBT
        # (Geuza Deni was used on at least one of them); otherwise report
        # whatever the (single, or first) tab's own status is.
        if any(t.status == 'OPEN' for t in tabs_found):
            effective_status = 'OPEN'
        elif outstanding > 0:
            effective_status = 'DEBT'
        else:
            effective_status = tabs_found[0].status

        # Representative customer for the debt-tracker lookup below — whichever
        # linked tab has one set. A correctly-merged bill's tabs should all
        # point at the same customer.
        rep_customer_id = next((t.customer_id for t in tabs_found if t.customer_id), None)

        # For DEBT tabs: the is_paid flags only flip when a payment FULLY covers
        # an entry. Partial cash/mpesa debt payments (recorded by staff in the
        # debt tracker) reduce the real balance without flipping any flag.
        # Pull the true outstanding from the debt tracker so partial payments
        # are reflected on the customer's live receipt immediately.
        if effective_status == 'DEBT' and rep_customer_id:
            try:
                from .models import Customer as _Customer
                from .debt_views import _get_customer_debt_data
                cust = _Customer.objects.filter(
                    id=rep_customer_id, business=receipt.business
                ).first()
                if cust:
                    # Scope to a single source only when exactly one tab is
                    # linked; a multi-tab (cross-counter merged) bill spans both.
                    tab_source = tabs_found[0].source if len(tabs_found) == 1 else 'all'
                    debt_data = _get_customer_debt_data(cust, receipt.business, scope=tab_source)
                    outstanding = float(debt_data.get('outstanding', outstanding))
                    # Debt fully cleared: receipt stops being live
                    if outstanding <= 0:
                        effective_status = 'SETTLED'
                        outstanding = 0.0
            except Exception:
                pass

        is_live = effective_status in ('OPEN', 'DEBT')
        return is_live, effective_status, lines, outstanding
    except Exception:
        return False, None, None, None


def _get_station_debt_data(receipt, live_lines):
    """Return per-station debt breakdown for the two-block DEBT payment UI.

    Returns a dict:
      {
        'bar':     {outstanding, total, paid, pct_paid, has_debt},
        'kitchen': {outstanding, total, paid, pct_paid, has_debt},
        'customer_phone': str,
      }
    Totals are computed from live_lines (all entries including paid ones) so
    the gauge always spans 0→original_total, not 0→current_outstanding.
    """
    empty = {'outstanding': 0.0, 'total': 0.0, 'paid': 0.0, 'pct_paid': 0, 'has_debt': False}
    result = {'bar': dict(empty), 'kitchen': dict(empty), 'customer_phone': ''}

    if not live_lines:
        return result

    for line in live_lines:
        key = 'kitchen' if line.get('is_kitchen') else 'bar'
        result[key]['total'] += float(line.get('subtotal', 0))

    all_tab_ids = _receipt_all_tab_ids(receipt)
    if not all_tab_ids:
        return result

    try:
        from .models import BarTab as _BTab, Customer as _Customer
        from .debt_views import _get_customer_debt_data
        tab = None
        for btab_id in all_tab_ids:
            _t = _BTab.objects.filter(id=btab_id, business=receipt.business).first()
            if _t and _t.customer_id:
                tab = _t
                break
        if not tab:
            return result

        cust = _Customer.objects.filter(id=tab.customer_id, business=receipt.business).first()
        if not cust:
            return result

        result['customer_phone'] = cust.phone or ''

        for key, scope in (('bar', 'bar'), ('kitchen', 'kitchen')):
            if result[key]['total'] <= 0:
                continue
            data = _get_customer_debt_data(cust, receipt.business, scope)
            owed = float(data.get('outstanding', 0))
            total = result[key]['total']
            paid = round(max(0.0, total - owed), 2)
            result[key].update({
                'outstanding': round(owed, 2),
                'paid': paid,
                'pct_paid': round(paid / total * 100) if total > 0 else 0,
                'has_debt': owed > 0,
            })
    except Exception:
        pass

    return result


def _pending_transfers_for_tabs(business, tab_ids):
    """Pending TabTransferRequests proposing to add money onto any of these
    tabs — i.e. incoming split-bill requests the customer needs to accept or
    reject (e.g. "Roy wants to add KES 200 to your tab for his Smirnoff").
    Shared by the receipt-based live view (_pending_transfers_in below) and
    tab_live_view — the fallback page for a tab that has no receipt yet at
    all, e.g. a brand-new tab just opened for a friend who wasn't already
    drinking (2026-07-24 live request). See BarTabEntry.split_and_transfer_
    locked() / TabTransferRequest in core/models.py.

    A "transfer whole tab" proposal (2026-07-25) creates one TabTransferRequest
    PER unpaid entry, all sharing one batch_id — grouped here into a single
    bundled dict so the customer sees and resolves ONE request ("Roy wants to
    transfer his whole tab — 3 items, KES 850"), not N separate confusing
    ones. Any one transfer_id in the group works to accept/reject the whole
    batch (TabTransferRequest.accept()/reject() already cascade to every
    sibling sharing batch_id), so the UI only needs the first id.
    """
    from .models import TabTransferRequest as _Transfer
    if not tab_ids:
        return []
    rows = list(
        _Transfer.objects.filter(
            dest_tab_id__in=tab_ids, status='PENDING', business=business,
        ).select_related('source_tab').order_by('id')
    )
    grouped = {}
    order = []
    for t in rows:
        key = t.batch_id or f'single-{t.id}'
        if key not in grouped:
            grouped[key] = {
                'batch_id': t.batch_id or None,
                'transfer_ids': [],
                'amount': 0.0,
                'paid_amount': 0.0,
                'items': [],
                'from_customer': t.source_tab.customer_name,
            }
            order.append(key)
        g = grouped[key]
        g['transfer_ids'].append(t.id)
        g['amount'] += float(t.amount)
        g['paid_amount'] += float(t.paid_amount)
        g['items'].append(t.note or 'kiingilio')

    result = []
    for key in order:
        g = grouped[key]
        is_whole_tab = bool(g['batch_id']) and len(g['transfer_ids']) > 1
        result.append({
            'id': g['transfer_ids'][0],
            'batch_id': g['batch_id'],
            'transfer_ids': g['transfer_ids'],
            'amount': g['amount'],
            'paid_amount': g['paid_amount'],
            'note': (f"tab yote — vitu {len(g['items'])}" if is_whole_tab else g['items'][0]),
            'from_customer': g['from_customer'],
            'is_whole_tab': is_whole_tab,
        })
    return result


def _pending_transfers_in(receipt):
    """Pending transfers targeting any of THIS receipt's live tabs."""
    return _pending_transfers_for_tabs(receipt.business, _receipt_all_tab_ids(receipt))


def public_receipt(request, token):
    receipt = get_object_or_404(Receipt, token=token)
    receipt_url = request.build_absolute_uri(request.path)

    # ── Live tab receipt: recompute lines from the BarTab for every request
    #    so the customer's QR-scanned page always shows the latest items,
    #    payments, and status without needing a new receipt link. ────────────
    is_live_tab, tab_status, live_lines, live_total = _get_live_tab_state(receipt)
    if live_lines is not None:
        receipt.lines = live_lines
        receipt.total = live_total
    elif not is_live_tab:
        # Not a tab-linked receipt at all — check whether any of its
        # direct-sale lines have since been voided/removed via the Recent
        # Payments panel (see _live_direct_lines' own docstring).
        _direct_lines = _live_direct_lines(receipt)
        if _direct_lines is not None:
            receipt.lines = _direct_lines
            receipt.total = round(sum(float(l.get('subtotal') or 0) for l in _direct_lines), 2)

    station_debt = None
    if tab_status == 'DEBT':
        station_debt = _get_station_debt_data(receipt, live_lines)

    pending_transfers_in = _pending_transfers_in(receipt) if is_live_tab else []

    # 2026-08-01 live request — Roy: the customer should see a running total
    # of what they've ALREADY paid on this tab, regardless of new items
    # being added since (a running tab's own "Jumla" only ever shows what's
    # still unpaid RIGHT NOW, per the DEBT-block wording above — there was
    # nothing showing the cumulative paid figure). Purely additive: sums
    # every already-paid line on the receipt as currently known; the live
    # poll (receipt_live_status → renderLines() in receipt_public.html)
    # recomputes the same figure from each fresh d.lines payload so it
    # keeps growing correctly as more of the tab gets settled over time.
    total_paid_so_far = round(
        sum(float(l.get('subtotal') or 0) for l in (receipt.lines or []) if l.get('is_paid')),
        2,
    )

    tab_pins = _receipt_tab_pin(receipt)

    return render(request, 'core/receipt_public.html', {
        'receipt':      receipt,
        'receipt_url':  receipt_url,
        'is_live_tab':  is_live_tab,
        'tab_status':   tab_status,
        'station_debt': station_debt,
        'pending_transfers_in': pending_transfers_in,
        'total_paid_so_far': total_paid_so_far,
        'tab_pins':     tab_pins,
        'other_debt':   _other_debt_link(receipt, tab_status),
    })


def _other_debt_link(receipt, tab_status):
    """{'amount', 'url'} when this customer ALSO owes money on a different,
    debt-converted tab -- otherwise None.

    2026-08-23 live request (Roy): "when someone's active tab receipt is
    shown and that same customer also has debt elsewhere, add a link from
    the active-tab receipt into their debt receipt (rather than showing both
    as separate rows in search)". Only fires for a receipt that is NOT itself
    the debt one, so a customer reading their debt receipt is never pointed
    back at itself.

    The amount shown prefers the debt tracker's own aggregate where a real
    Customer record exists: BarTabEntry.is_paid alone stays False until a
    line is FULLY covered, so it would overstate the balance after a genuine
    partial payment -- the same staleness _get_live_tab_state and
    _debt_converted_tabs_qs both already correct for elsewhere.

    Never raises -- a convenience cross-link must never be able to take down
    the customer's own bill.
    """
    if tab_status == 'DEBT':
        return None
    name = (receipt.customer_name or '').strip()
    if not name:
        return None
    try:
        from .models import BarTab, BarTabEntry, Customer
        from core.keg_views import _resolve_tab_public_url
        from core.debt_views import _get_customer_debt_data

        own_tab_ids = _receipt_all_tab_ids(receipt)
        debt_tab = (
            BarTab.objects
            .filter(business=receipt.business, customer_name__iexact=name)
            .exclude(id__in=own_tab_ids)
            .exclude(status__in=['OPEN', 'VOID'])
            .filter(id__in=BarTabEntry.objects.filter(is_paid=False)
                    .exclude(payment_method='void').values('tab_id'))
            .order_by('-opened_at')
            .first()
        )
        if debt_tab is None:
            return None
        url = _resolve_tab_public_url(debt_tab)
        if not url:
            return None

        amount = float(debt_tab.unpaid_total() or 0)
        if debt_tab.customer_id:
            cust = Customer.objects.filter(pk=debt_tab.customer_id).first()
            if cust is not None:
                amount = float(
                    _get_customer_debt_data(cust, receipt.business, scope='all')['outstanding']
                )
        if amount <= 0:
            return None
        return {'amount': round(amount, 2), 'url': url}
    except Exception:
        logger.exception('other-debt cross-link failed for receipt %s', receipt.id)
        return None


def receipt_live_status(request, token):
    """AJAX polling endpoint for the live receipt page.

    Returns the current tab state as JSON so the client can update the DOM
    without a full page reload. No auth required — token is the secret.
    Includes station debt breakdown when tab_status=='DEBT' so JS can update
    the per-station gauges and outstanding labels without a full page reload.
    """
    receipt = get_object_or_404(Receipt, token=token)
    is_live, tab_status, lines, total = _get_live_tab_state(receipt)
    if lines is None:
        # Not a tab-linked receipt — still check whether a direct-sale
        # line has since been voided (see _live_direct_lines' docstring),
        # so the 20s live poll self-heals a removal without needing a
        # full page reload.
        _direct_lines = _live_direct_lines(receipt)
        if _direct_lines is not None:
            _direct_total = round(sum(float(l.get('subtotal') or 0) for l in _direct_lines), 2)
            return JsonResponse({
                'is_live': False, 'tab_status': tab_status,
                'lines': _direct_lines, 'total': _direct_total,
            })
        return JsonResponse({'is_live': False, 'tab_status': tab_status})

    response = {
        'is_live':    is_live,
        'tab_status': tab_status,
        'lines':      lines,
        'total':      total,
        'pending_transfers_in': _pending_transfers_in(receipt) if is_live else [],
    }

    if tab_status == 'DEBT' or (not is_live and tab_status == 'SETTLED'):
        sd = _get_station_debt_data(receipt, lines)
        response['bar_outstanding']     = sd['bar']['outstanding']
        response['bar_pct_paid']        = sd['bar']['pct_paid']
        response['bar_has_debt']        = sd['bar']['has_debt']
        response['kitchen_outstanding'] = sd['kitchen']['outstanding']
        response['kitchen_pct_paid']    = sd['kitchen']['pct_paid']
        response['kitchen_has_debt']    = sd['kitchen']['has_debt']

    return JsonResponse(response)


@csrf_exempt
def receipt_respond_tab_transfer(request, token, transfer_id):
    """Customer-initiated accept/reject of a pending split-bill transfer, from
    their own public receipt page. No auth required — the receipt token is
    the customer's proof they're the one whose tab this concerns, same
    security model as receipt_pay(). See BarTabEntry.split_and_transfer_locked
    / TabTransferRequest in core/models.py for the full mechanism.

    POST { "action": "accept"|"reject" }
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)

    receipt = get_object_or_404(Receipt, token=token)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        data = request.POST

    action = (data.get('action') or '').strip()
    if action not in ('accept', 'reject'):
        return JsonResponse({'ok': False, 'error': 'action lazima iwe accept au reject'}, status=400)

    from .models import TabTransferRequest as _Transfer
    transfer = get_object_or_404(_Transfer, id=transfer_id, business=receipt.business)

    # This receipt must actually be the destination tab's own receipt — a
    # transfer for a tab this token has no claim over must not be actionable
    # from here (mirrors the tab-ownership check every other public receipt
    # action in this file does via _receipt_all_tab_ids).
    if transfer.dest_tab_id not in _receipt_all_tab_ids(receipt):
        return JsonResponse({'ok': False, 'error': 'Ombi hili si la risiti hii.'}, status=403)

    try:
        if action == 'accept':
            transfer.accept()
        else:
            transfer.reject()
    except ValueError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)

    _notify_tab_transfer_resolved(transfer)

    return JsonResponse({'ok': True, 'status': transfer.status})


@login_required
@require_POST
def send_receipt(request, receipt_id):
    user_profile = get_user_profile(request)
    if not user_profile:
        return JsonResponse({'ok': False, 'error': 'not authenticated'}, status=403)

    receipt = get_object_or_404(Receipt, id=receipt_id, business=user_profile.business)
    receipt_url = request.build_absolute_uri(f'/r/{receipt.token}/')

    channel = request.POST.get('channel', 'sms')
    phone = request.POST.get('phone', '').strip()
    email = request.POST.get('email', '').strip()

    if channel == 'sms':
        if not phone:
            phone = receipt.customer_phone
        normalized = normalize_ke_phone(phone) if phone else None
        if not normalized:
            return JsonResponse({'ok': False, 'error': 'invalid_phone'})
        business = user_profile.business
        msg = (
            f"Risiti #{receipt.receipt_number} — {business.name}\n"
            f"Jumla: KES {receipt.total:,.0f}\n"
            f"Angalia: {receipt_url}"
        )
        ok, at_detail = send_sms_notification(msg, normalized)
        if ok:
            return JsonResponse({'ok': True})
        return JsonResponse({'ok': False, 'error': 'sms_failed', 'detail': at_detail})

    if channel == 'email':
        if not email:
            email = receipt.customer_name  # fallback, but really should be an email field
        if not email or '@' not in email:
            return JsonResponse({'ok': False, 'error': 'invalid_email'})
        business = user_profile.business
        lines_html = ''.join(
            f'<tr><td>{l["name"]}</td><td style="text-align:right">×{l.get("qty",1)}</td>'
            f'<td style="text-align:right">KES {float(l.get("subtotal",0)):,.0f}</td></tr>'
            for l in receipt.lines
        )
        html = f"""
        <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:24px;">
          <h2 style="color:#c9a84c;">{business.name}</h2>
          <p style="color:#666;">Risiti #{receipt.receipt_number} &mdash; {receipt.created_at.strftime('%d %b %Y, %H:%M')}</p>
          <table style="width:100%;border-collapse:collapse;font-size:14px;">
            <thead><tr style="border-bottom:2px solid #eee;">
              <th style="text-align:left;padding:6px 4px;">Item</th>
              <th style="text-align:right;padding:6px 4px;">Qty</th>
              <th style="text-align:right;padding:6px 4px;">Subtotal</th>
            </tr></thead>
            <tbody>{lines_html}</tbody>
            <tfoot><tr style="border-top:2px solid #eee;font-weight:bold;">
              <td colspan="2" style="padding:8px 4px;">Total</td>
              <td style="text-align:right;padding:8px 4px;">KES {receipt.total:,.0f}</td>
            </tr></tfoot>
          </table>
          <p style="margin-top:20px;font-size:13px;color:#888;">
            Malipo: {receipt.payment_method.upper()}<br>
            <a href="{receipt_url}" style="color:#c9a84c;">Angalia risiti online</a>
          </p>
        </div>
        """
        ok = send_email_notification(
            to_email=email,
            subject=f"Risiti #{receipt.receipt_number} — {business.name}",
            html_message=html,
            text_message=f"Risiti #{receipt.receipt_number} — {business.name}\nJumla: KES {receipt.total:,.0f}\n{receipt_url}",
        )
        return JsonResponse({'ok': bool(ok)})

    return JsonResponse({'ok': False, 'error': 'unknown_channel'})


def _fire_cash_payment_request(business, tab_ids, customer_name, amount, sources=None):
    """Notify staff that a customer intends to pay cash at the counter.

    No money moves here — this is a heads-up only, fired from the customer's
    live receipt page. Mirrors the recipient pattern already used for
    debt-payment notifications (_settle_receipt_entries_from_payment in
    mpesa_views.py): original serving staff, current on-shift staff, owners
    and managers, via in-app + SMS.

    `sources` is the set of BarTab.source values ('bar'/'kitchen'/'qs') this
    cash request concerns, used to station-scope the "current on-shift staff"
    fan-out via _station_scope() — a kitchen-only staffer must not be pinged
    about a bar tab, and vice versa (Station Scoping Principle, CLAUDE.md).
    Callers with a live tab don't need to pass this — the tab's own `source`
    is read below and merged in automatically; debt-mode callers (no live
    tab) should pass the debt ledger's source explicitly. 'qs' and unknown/
    empty sources are left unscoped (Quick Sell tabs aren't station-specific).
    """
    from .models import BarTab as _BarTab, Notification as _Notif
    from .notifications import normalize_ke_phone, send_sms_notification, send_sms_notification_async
    from accounts.models import UserProfile as _UP
    from .views import scoped_on_shift_targets

    msg = f"💵 {customer_name or 'Mteja'} anataka kulipa CASH — KES {amount:,.0f}. Mngoje kwenye counter."

    notify_targets = {}  # user_pk -> UserProfile
    sources = set(sources or [])

    for tab_id in tab_ids:
        tab_obj = _BarTab.objects.filter(id=tab_id, business=business).select_related('served_by').first()
        if not tab_obj:
            continue
        sources.add(tab_obj.source)
        if tab_obj.served_by_id:
            up = _UP.objects.filter(user_id=tab_obj.served_by_id, business=business).first()
            if up:
                notify_targets[tab_obj.served_by_id] = up

    notify_targets.update(scoped_on_shift_targets(business, sources))

    for up in _UP.objects.filter(business=business, role__in=['owner', 'manager']):
        notify_targets[up.user_id] = up

    _cash_link = '/kitchen/' if (sources == {'kitchen'}) else '/bar/'
    for up in notify_targets.values():
        _Notif.objects.create(
            user=up.user, title='💵 Mteja anataka kulipa Cash',
            message=msg, notification_type='warning', link_url=_cash_link,
        )
        phone = (up.phone or '').strip()
        if phone:
            phone_n = normalize_ke_phone(phone)
            if phone_n:
                send_sms_notification_async(msg, phone_n)


def _notify_tab_transfer_resolved(transfer):
    """Notify staff once a customer accepts or rejects a pending split-bill
    transfer (BarTabEntry.split_and_transfer_locked / TabTransferRequest).
    Same recipient pattern as _fire_cash_payment_request above: the staff
    member who requested the transfer, everyone currently on shift, and
    owners/managers — via in-app + SMS. A REJECTED transfer especially needs
    this: the money is still sitting on the source customer's own tab
    unresolved, and someone needs to know to go collect it from them
    directly."""
    from .models import Notification as _Notif
    from accounts.models import UserProfile as _UP

    business = transfer.business

    # A whole-tab transfer resolves as one bundled decision across every
    # sibling row sharing batch_id (accept()/reject() already cascade them
    # together) — summarize the aggregate here too, not just the one
    # TabTransferRequest object the caller happened to pass in, otherwise
    # this would only report ONE item's amount out of the whole tab.
    if transfer.batch_id:
        from .models import TabTransferRequest as _Transfer
        siblings = list(_Transfer.objects.filter(batch_id=transfer.batch_id, business=business))
        from decimal import Decimal as _Decimal
        total_amount = sum((s.amount for s in siblings), _Decimal('0'))
        item_desc = f'tab yote — vitu {len(siblings)}'
    else:
        total_amount = transfer.amount
        item_desc = transfer.note

    if transfer.status == 'ACCEPTED':
        msg = (
            f"✅ {transfer.dest_tab.customer_name} amekubali KES {total_amount:,.0f} "
            f"({item_desc}) — imehamishiwa tab yake."
        )
        title = '✅ Uhamisho wa Bili Umekubaliwa'
    else:
        msg = (
            f"❌ {transfer.dest_tab.customer_name} amekataa KES {total_amount:,.0f} "
            f"({item_desc}) — deni limerudi kwa {transfer.source_tab.customer_name}."
        )
        title = '❌ Uhamisho wa Bili Umekataliwa'

    notify_targets = {}
    if transfer.requested_by_id:
        up = _UP.objects.filter(user_id=transfer.requested_by_id, business=business).first()
        if up:
            notify_targets[transfer.requested_by_id] = up

    # A transfer can cross counters (Roy pays on his Bar tab, Bosco covers it
    # on his own Kitchen tab) — station-scope against BOTH sides, so a
    # bar-only staffer still hears about it if either tab touches their
    # counter, matching this app's Station Scoping Principle.
    from .views import scoped_on_shift_targets
    _sources = {transfer.source_tab.source, transfer.dest_tab.source}
    notify_targets.update(scoped_on_shift_targets(business, _sources))

    for up in _UP.objects.filter(business=business, role__in=['owner', 'manager']):
        notify_targets[up.user_id] = up

    _board_by_source = {'bar': '/bar/', 'kitchen': '/kitchen/', 'qs': '/quick-sell/'}
    _transfer_link = _board_by_source.get(transfer.source_tab.source or 'bar', '/bar/')
    for up in notify_targets.values():
        _Notif.objects.create(user=up.user, title=title, message=msg, notification_type='info',
                              link_url=_transfer_link)
        phone = (up.phone or '').strip()
        if phone:
            phone_n = normalize_ke_phone(phone)
            if phone_n:
                send_sms_notification_async(msg, phone_n)


@csrf_exempt
def receipt_pay(request, token):
    """Customer-initiated payment from the public receipt page.

    No auth required — token is the secret. Supports:
      - type=stk : initiate M-Pesa STK Push (requires phone)
      - type=qr  : return EMVCo QR string for the amount (no phone needed)

    POST JSON:
      { "type": "stk"|"qr", "entry_ids": [1,2,3], "phone": "0712345678" }
      entry_ids empty = pay all unpaid entries on the receipt.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    receipt = get_object_or_404(Receipt, token=token)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    pay_type  = data.get('type', 'stk')
    entry_ids = data.get('entry_ids') or []
    phone     = (data.get('phone') or receipt.customer_phone or '').strip()

    business    = receipt.business
    all_tab_ids = _receipt_all_tab_ids(receipt)

    from .mpesa import resolve_mpesa_config

    # ── Debt block mode: customer paying a summary amount per station ──────
    # Triggered when 'source' is in the request body (no entry_ids needed).
    # This creates a CustomerDebtPayment on callback instead of marking entries.
    debt_source = data.get('source')  # 'bar' or 'kitchen'
    is_debt_mode = bool(debt_source)

    # Entry mode needs a real tab to select unpaid entries from — debt mode
    # doesn't (it pays a summary amount off the debt tracker directly). Was
    # previously gated unconditionally on receipt.meta.tab_id alone (before
    # even parsing is_debt_mode), which 400'd every payment attempt — STK, QR,
    # AND cash — for a receipt reached only via linked_tab_ids (see
    # _receipt_all_tab_ids docstring).
    if not is_debt_mode and not all_tab_ids:
        return JsonResponse({'error': 'not_a_tab'}, status=400)

    if is_debt_mode:
        try:
            amount = int(float(data.get('debt_amount', 0)))
        except (TypeError, ValueError):
            amount = 0
        if amount < 1:
            return JsonResponse({'error': 'nothing_to_pay'}, status=400)
        selected_ids = None  # None = debt mode discriminator in callback

        # Route to the station's own M-Pesa if configured
        target_store = None
        try:
            from .models import Store as _Store
            target_store = _Store.objects.filter(
                business=business,
                is_kitchen=(debt_source == 'kitchen'),
                has_own_mpesa=True,
            ).first()
        except Exception:
            pass
    else:
        # ── Entry-based mode: customer selects specific items ──────────────
        from .models import BarTabEntry
        if entry_ids:
            entries_qs = BarTabEntry.objects.filter(
                id__in=entry_ids,
                tab__id__in=all_tab_ids,
                is_paid=False,
            )
        else:
            entries_qs = BarTabEntry.objects.filter(tab__id__in=all_tab_ids, is_paid=False)

        # 2026-08-24 audit — remaining_amount(), never the full `amount`.
        # This is what the CUSTOMER actually gets charged (STK Push) or told
        # to hand over at the counter, so reading the entry's original price
        # on a partly-paid entry (e.g. an 80 KES cup with 30 already
        # collected via a debt-tracker payment before it was transferred
        # here) overcharges them by exactly the already-collected portion.
        # Entries whose balance is already fully covered are dropped rather
        # than billed at zero.
        entries_list = [
            e for e in entries_qs.select_related('transaction__item__store')
            if e.remaining_amount() > 0
        ]
        amount       = int(sum(float(e.remaining_amount()) for e in entries_list))
        selected_ids = [e.id for e in entries_list]

        if amount < 1:
            return JsonResponse({'error': 'nothing_to_pay'}, status=400)

        # Station-aware M-Pesa routing for entry mode
        target_store = None
        store_ids = set()
        for e in entries_list:
            try:
                store_ids.add(e.transaction.item.store_id)
            except Exception:
                pass
        if len(store_ids) == 1:
            sid = next(iter(store_ids))
            if sid:
                from .models import Store as _Store
                s = _Store.objects.filter(id=sid).first()
                if s and getattr(s, 'has_own_mpesa', False):
                    target_store = s

    if pay_type == 'qr':
        try:
            from .mpesa import generate_emv_qr_string
            cfg = resolve_mpesa_config(business, target_store)
            use_till = bool(cfg.get('till'))
            shortcode = (cfg.get('till') if use_till else cfg.get('paybill') or '').strip()
            if not shortcode:
                return JsonResponse({'error': 'no_mpesa_config'}, status=400)
            trx_code = 'BG' if use_till else 'PB'
            qr_string = generate_emv_qr_string(
                merchant_name=business.name,
                shortcode=shortcode,
                trx_code=trx_code,
                amount=amount,
            )
            return JsonResponse({'ok': True, 'type': 'qr', 'qr_data': qr_string, 'amount': amount})
        except Exception:
            logger.exception('receipt_pay QR failed token=%s', token)
            return JsonResponse({'error': 'qr_failed'}, status=500)

    if pay_type == 'cash':
        # No money moves — flag the tab(s) so staff see a badge in the tabs
        # drawer, and fire a heads-up notification. Actual settlement still
        # happens at the counter through the existing settle_tab/tick_entry
        # flow, which clears the flag.
        tab_ids_for_flag = set()
        cash_sources = set()
        if not is_debt_mode:
            tab_ids_for_flag = {e.tab_id for e in entries_list}
            if tab_ids_for_flag:
                from .models import BarTab as _BarTab
                from django.utils import timezone as _tz
                _BarTab.objects.filter(id__in=tab_ids_for_flag).update(cash_requested_at=_tz.now())
        elif debt_source in ('bar', 'kitchen'):
            cash_sources = {debt_source}

        # Cooldown on the notification fan-out only (not on the flag update
        # above) — this endpoint is public/unauthenticated and the "Lipa Cash"
        # button has no client-side idempotency token, so a customer tapping
        # repeatedly (impatience, confusion, or a stray bot hit) would
        # otherwise fire a fresh in-app + SMS notification to every serving
        # staff, on-shift staff, and owner/manager on every single tap. Mirrors
        # the 10-min SMS-bundling convention already used elsewhere in this
        # app (Business.last_txn_sms_at).
        from django.core.cache import cache
        _cooldown_key = f'cash_request_notif:{token}'
        _notify_now = cache.add(_cooldown_key, True, timeout=600)
        if _notify_now:
            try:
                _fire_cash_payment_request(
                    business, tab_ids_for_flag, receipt.customer_name, amount, sources=cash_sources,
                )
            except Exception:
                logger.exception('receipt_pay cash notify failed token=%s', token)
        return JsonResponse({'ok': True, 'type': 'cash', 'amount': amount})

    # STK Push
    if not phone:
        return JsonResponse({'error': 'phone_required'}, status=400)

    # Server-side double-submit backstop — see core/idempotency.py. This is a
    # public/unauthenticated endpoint (the customer's own phone triggers the
    # STK prompt); the client already disables its button on click, but that
    # only guards a second click on the same live page, not a genuine
    # duplicate request reaching the server.
    from core.idempotency import claim_checkout_token
    idem_token = (data.get('idempotency_token') or '').strip()
    if not claim_checkout_token(business.id, idem_token):
        return JsonResponse({'error': 'STK Push hii tayari imetumwa.', 'duplicate': True}, status=409)

    try:
        from .mpesa import initiate_stk_push, format_phone_ke
        from .models import Payment

        phone_fmt = format_phone_ke(phone)
        cfg = resolve_mpesa_config(business, target_store)
        use_till = bool(cfg.get('till'))
        shortcode = (cfg.get('till') if use_till else cfg.get('paybill') or '').strip()
        if not shortcode:
            return JsonResponse({'error': 'no_mpesa_config'}, status=400)

        callback_url = request.build_absolute_uri('/mpesa/callback/')
        result = initiate_stk_push(
            phone_number=phone_fmt,
            amount=amount,
            account_reference=f"RCPT-{receipt.receipt_number}",
            description="Duka Mwecheche",
            callback_url=callback_url,
            consumer_key=cfg.get('consumer_key') or None,
            consumer_secret=cfg.get('consumer_secret') or None,
            shortcode=shortcode,
            passkey=cfg.get('passkey') or None,
            use_till=use_till,
            env=cfg.get('environment', 'sandbox'),
        )

        if not result or result.get('ResponseCode') != '0':
            err = result.get('ResponseDescription', 'STK failed') if result else 'No response from Daraja'
            return JsonResponse({'error': err}, status=400)

        payment = Payment.objects.create(
            business=business,
            store=cfg.get('store'),
            source=debt_source or cfg.get('source', 'bar'),
            amount=amount,
            method='mpesa',
            status='pending',
            phone=phone_fmt,
            checkout_request_id=result.get('CheckoutRequestID', ''),
            merchant_request_id=result.get('MerchantRequestID', ''),
            tab_entry_ids=selected_ids,  # None for debt mode, list for entry mode
            receipt_token=token,
        )
        return JsonResponse({
            'ok': True,
            'type': 'stk',
            'payment_id': payment.id,
            'checkout_request_id': payment.checkout_request_id,
            'amount': amount,
        })
    except Exception:
        logger.exception('receipt_pay STK failed token=%s', token)
        return JsonResponse({'error': 'stk_failed'}, status=500)


def tab_live_view(request, token):
    """Public live-bill page for a bar tab — no login required.

    Reached by scanning the wall QR → find_tab page → typing name or PIN.
    Shows all non-voided tab entries with running total and outstanding balance.
    Auto-refreshes every 20 seconds so customers see new items as they're added.
    """
    from decimal import Decimal as _Decimal
    from .models import BarTab as _BarTab
    tab = get_object_or_404(_BarTab, tab_receipt_token=token)
    entries = (
        tab.entries
        .exclude(payment_method='void')
        .order_by('id')
        .select_related('transaction__item__store')
    )
    lines = []
    outstanding = _Decimal('0')
    for e in entries:
        try:
            is_k = e.transaction.item.store.is_kitchen
        except Exception:
            is_k = False
        icon = '🍽 ' if is_k else '🍺 '
        amt = e.amount
        if not e.is_paid:
            outstanding += amt
        lines.append({
            'icon': icon,
            'description': e.description,
            'amount': float(amt),
            'is_paid': e.is_paid,
        })
    total_val = sum(_Decimal(str(l['amount'])) for l in lines)
    return render(request, 'core/tab_live.html', {
        'tab': tab,
        'business': tab.business,
        'lines': lines,
        'outstanding': float(outstanding),
        'total': float(total_val),
        'is_settled': tab.status in ('SETTLED', 'VOID'),
        'pending_transfers_in': _pending_transfers_for_tabs(tab.business, [tab.id]),
    })


@csrf_exempt
def tab_respond_tab_transfer(request, token, transfer_id):
    """Customer-initiated accept/reject of a pending split-bill transfer, from
    the bare tab_live_view page — used when the destination tab has no
    receipt yet at all (e.g. a brand-new tab just opened for a friend who
    wasn't already drinking, 2026-07-24 live request), so
    receipt_respond_tab_transfer (keyed off a Receipt token) doesn't apply.
    Same security model — the tab's own token is the customer's proof this
    concerns them. POST { "action": "accept"|"reject" }
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)

    from .models import BarTab as _BarTab, TabTransferRequest as _Transfer
    tab = get_object_or_404(_BarTab, tab_receipt_token=token)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        data = request.POST

    action = (data.get('action') or '').strip()
    if action not in ('accept', 'reject'):
        return JsonResponse({'ok': False, 'error': 'action lazima iwe accept au reject'}, status=400)

    transfer = get_object_or_404(_Transfer, id=transfer_id, business=tab.business, dest_tab_id=tab.id)

    try:
        if action == 'accept':
            transfer.accept()
        else:
            transfer.reject()
    except ValueError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)

    _notify_tab_transfer_resolved(transfer)

    return JsonResponse({'ok': True, 'status': transfer.status})
