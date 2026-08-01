"""Custom AdminSite for Duka Mwecheche's platform admin (/admin/).

2026-08-01 — Roy (the app's developer) asked for a redesigned admin console:
clear "which business owns this record" visibility everywhere, and a live
Platform Overview so he can see how many businesses are registered and what
they're doing right now, without hunting through the default flat app list.

`duka_admin_site` shares the SAME registry dict object as Django's default
`admin.site` (aliased below), so every existing `@admin.register(...)` call
in accounts/admin.py and core/admin.py keeps working completely unchanged —
this file never needs to know what models exist. Because `admin.site.register()`
mutates that dict in place rather than replacing it, the alias is safe
regardless of whether this module is imported before or after
accounts/admin.py and core/admin.py have run.
"""
from collections import defaultdict
from datetime import timedelta

from django.contrib import admin
from django.db.models import Max, Q
from django.utils import timezone


class DukaAdminSite(admin.AdminSite):
    site_header = 'Duka Mwecheche — Platform Admin'
    site_title = 'Duka Mwecheche Admin'
    index_title = 'Platform Overview'

    def index(self, request, extra_context=None):
        extra_context = dict(extra_context or {})
        try:
            extra_context.update(_build_dashboard_context())
        except Exception:
            # A dashboard-query failure must never take down the whole admin
            # index page (the one page every admin action starts from).
            extra_context.setdefault('duka_dashboard_error', True)
        return super().index(request, extra_context)


duka_admin_site = DukaAdminSite(name='duka_admin')
duka_admin_site._registry = admin.site._registry


def _build_dashboard_context():
    from accounts.models import Business
    from core.models import Transaction, Shift, BarTab

    now = timezone.now()
    week_ago = now - timedelta(days=7)

    businesses = list(
        Business.objects.select_related('owner', 'business_type').order_by('-created_at')
    )
    total_businesses = len(businesses)
    new_this_week = sum(1 for b in businesses if b.created_at and b.created_at >= week_ago)

    open_now = []
    for b in businesses:
        try:
            if b.is_open():
                open_now.append(b)
        except Exception:
            pass

    recent_txns = list(
        Transaction.objects.select_related('business', 'item')
        .order_by('-created_at')[:20]
    )
    activity_feed = []
    for t in recent_txns:
        try:
            revenue = t.revenue()
        except Exception:
            revenue = None
        activity_feed.append({
            'business': t.business,
            'item': t.item,
            'type': t.type,
            'revenue': revenue,
            'created_at': t.created_at,
        })

    open_shifts_qs = (
        Shift.objects.filter(status='OPEN')
        .select_related('business', 'staff')
        .order_by('business__name')
    )
    open_shifts_count = open_shifts_qs.count()

    open_tabs_qs = (
        BarTab.objects.filter(status='OPEN')
        .select_related('business')
        .order_by('business__name')
    )
    open_tabs_count = open_tabs_qs.count()
    open_tabs_by_business = {}
    for tab in open_tabs_qs:
        entry = open_tabs_by_business.setdefault(
            tab.business_id, {'business': tab.business, 'count': 0}
        )
        entry['count'] += 1

    quiet_businesses = (
        Business.objects.annotate(last_txn=Max('transactions__created_at'))
        .filter(Q(last_txn__lt=week_ago) | Q(last_txn__isnull=True))
        .order_by('last_txn')
    )

    # ── Chart data ──────────────────────────────────────────────────────
    today = timezone.localdate()

    # New business registrations, last 12 weeks (Mon-Sun buckets), reusing
    # the already-fetched `businesses` list rather than re-querying.
    weekly_registrations = []
    for i in range(11, -1, -1):
        week_start = today - timedelta(days=today.weekday() + 7 * i)
        week_end = week_start + timedelta(days=6)
        count = sum(
            1 for b in businesses
            if b.created_at and week_start <= timezone.localtime(b.created_at).date() <= week_end
        )
        weekly_registrations.append({'label': week_start.strftime('%b %d'), 'value': count})

    # Businesses by type — single-hue magnitude-by-category, no color-class
    # limit applies since only one bar color is used throughout.
    type_counts = defaultdict(int)
    for b in businesses:
        label = b.business_type.name if b.business_type else 'Unspecified'
        type_counts[label] += 1
    business_type_breakdown = sorted(
        ({'label': k, 'value': v} for k, v in type_counts.items()),
        key=lambda r: -r['value'],
    )

    # Platform-wide daily revenue, last 14 days — Transaction.revenue() per
    # row (never raw Sum('sale_amount'), see CLAUDE.md), bucketed by date.
    window_start = today - timedelta(days=13)
    daily_txns = (
        Transaction.objects.filter(type='Issue', date__gte=window_start, date__lte=today)
        .select_related('item')
    )
    daily_totals = defaultdict(float)
    for t in daily_txns:
        try:
            daily_totals[t.date] += (t.revenue() or 0)
        except Exception:
            pass
    daily_revenue = []
    d = window_start
    while d <= today:
        daily_revenue.append({'label': d.strftime('%b %d'), 'value': round(daily_totals.get(d, 0))})
        d += timedelta(days=1)

    return {
        'duka_total_businesses': total_businesses,
        'duka_new_this_week': new_this_week,
        'duka_open_now_count': len(open_now),
        'duka_open_now': open_now[:30],
        'duka_activity_feed': activity_feed,
        'duka_open_shifts': list(open_shifts_qs[:30]),
        'duka_open_shifts_count': open_shifts_count,
        'duka_open_tabs': sorted(
            open_tabs_by_business.values(), key=lambda e: e['business'].name
        ),
        'duka_open_tabs_count': open_tabs_count,
        'duka_quiet_businesses': list(quiet_businesses[:30]),
        'duka_chart_weekly_registrations': weekly_registrations,
        'duka_chart_business_types': business_type_breakdown,
        'duka_chart_daily_revenue': daily_revenue,
    }
