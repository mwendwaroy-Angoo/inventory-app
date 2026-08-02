"""
UBA §5.3 — "Maduka Yangu" (My Shops), the owner console for a multi-store
business. Route /maduka/, owner-only per the spec's own text ("owner only").

Scope of this pass (documented deferral, same discipline already used for
M0-5/M0-6's dashboard-tile/analytics-section registries and M1's navbar
switcher): the day strip, per-store cards (sorted problem-first) and the
exception feed are built and wired to real data. The side-by-side "compare
view" (spec's item 4, a Chart.js multi-store comparison) is NOT built this
pass — it is a template-heavy feature that needs visual verification this
environment cannot provide, matching the exact reasoning already logged for
M0-5/M0-6. Logged as a follow-up, not silently skipped.

"Who is on shift right now" / "cash expected vs counted" are only meaningful
for a store using the existing bar/kitchen Shift system (station='bar' or
station='kitchen', see shift_views._shift_station()) — Shift has no genuine
per-store dimension yet for a business with N real retail outlets (Shift.store
is set to business.stores.first() at open_shift() time regardless of which
actual store, a pre-existing limitation flagged in the M1 Cause-&-Effect map
as "VERIFY-ME"). Retail/produce/salon/rental/warehouse-type stores correctly
show revenue-vs-target only, with no fabricated shift/till figures — honest
about what this app can and cannot currently attribute per physical outlet.
"""
from django.contrib.auth.decorators import login_required
from django.db.models import Count, F
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import BarTab, BusinessException, RevenueTarget, Shift, Store, Transaction
from .views import owner_required


def _station_for_store(store):
    if store.is_kitchen:
        return 'kitchen'
    if store.store_type == 'bar':
        return 'bar'
    return None


@login_required
@owner_required
def maduka_dashboard(request):
    from .shift_views import _shift_station, till_expected_cash

    up = request.user.userprofile
    business = up.business
    today = timezone.localdate()

    stores = list(Store.objects.filter(business=business, is_active=True).order_by('name'))

    today_txns = list(
        Transaction.objects.filter(business=business, type='Issue', date=today)
        .exclude(payment_method='void')
        .exclude(invoice_no='[SVQ]')
        .select_related('item')
    )
    total_revenue_today = sum((float(t.revenue()) for t in today_txns), 0.0)
    credit_issued_today = sum(
        (float(t.revenue()) for t in today_txns if t.payment_method == 'credit'), 0.0
    )

    daily_targets = list(RevenueTarget.objects.filter(business=business, target_type='daily'))
    store_targets = {t.store_id: float(t.amount) for t in daily_targets if t.store_id}
    business_target = next((float(t.amount) for t in daily_targets if t.store_id is None), 0.0)
    combined_target = sum(store_targets.values()) or business_target

    open_tabs_kes = sum(
        (float(tab.unpaid_total()) for tab in BarTab.objects.filter(business=business, status='OPEN')),
        0.0,
    )

    open_shifts = list(
        Shift.objects.filter(business=business, status='OPEN').select_related('staff')
    )
    exc_counts = {
        row['store_id']: row['n']
        for row in BusinessException.objects.filter(
            business=business, acknowledged_at__isnull=True
        ).values('store_id').annotate(n=Count('id'))
    }

    store_cards = []
    for store in stores:
        store_revenue = sum(
            (float(t.revenue()) for t in today_txns if t.item.store_id == store.id), 0.0
        )
        store_target = store_targets.get(store.id)
        exc_count = exc_counts.get(store.id, 0)

        station = _station_for_store(store)
        on_shift_names = []
        till_info = None
        if station:
            matching = [s for s in open_shifts if _shift_station(s) == station]
            on_shift_names = [
                (s.staff.get_full_name() or s.staff.username) for s in matching
            ]
            till_info = till_expected_cash(business, station)

        store_cards.append({
            'store': store,
            'is_open_for_business': bool(on_shift_names) if station else store.is_active,
            'on_shift_names': on_shift_names,
            'station': station,
            'revenue_today': float(store_revenue),
            'target_today': store_target,
            'till_info': till_info,
            'exc_count': exc_count,
        })

    # Problem-first sort: most unacknowledged exceptions first, then whichever
    # store is furthest below its own target (only where a target is set),
    # ties broken alphabetically for a stable, predictable order.
    def _sort_key(card):
        pct_short = 0.0
        if card['target_today']:
            pct_short = max(0.0, 1 - (card['revenue_today'] / card['target_today']))
        return (-card['exc_count'], -pct_short, card['store'].name)

    store_cards.sort(key=_sort_key)

    exceptions = list(
        BusinessException.objects.filter(business=business)
        .select_related('store', 'staff__user', 'shift')
        .order_by(F('acknowledged_at').asc(nulls_first=True), '-created_at')[:50]
    )

    context = {
        'business': business,
        'today': today,
        'total_revenue_today': float(total_revenue_today),
        'combined_target': combined_target,
        'open_tabs_kes': float(open_tabs_kes),
        'credit_issued_today': float(credit_issued_today),
        'store_cards': store_cards,
        'exceptions': exceptions,
    }
    return render(request, 'core/maduka.html', context)


@login_required
@owner_required
@require_POST
def maduka_acknowledge_exception(request, exc_id):
    exc = get_object_or_404(BusinessException, id=exc_id, business=request.user.userprofile.business)
    exc.acknowledge(request.user.userprofile)
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
        return JsonResponse({'ok': True})
    from django.shortcuts import redirect
    return redirect('maduka_dashboard')
