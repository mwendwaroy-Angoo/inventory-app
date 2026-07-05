"""Promo / Broadcast module — send targeted messages to the customer database."""

import datetime

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import BarTab, Customer, Notification, PromoMessage, Transaction
from .notifications import normalize_ke_phone, send_sms_notification


def _get_up(request):
    try:
        return request.user.userprofile
    except Exception:
        return None


def _owner_required(request):
    up = _get_up(request)
    return up and getattr(up, 'is_owner', False)


# ── Customer database list ────────────────────────────────────────────────────

@login_required
def promo_customer_db(request):
    up = _get_up(request)
    if not up:
        return redirect('login')

    business = up.business
    search = (request.GET.get('q') or '').strip()

    customers = Customer.objects.filter(business=business)
    if search:
        customers = customers.filter(
            Q(name__icontains=search) | Q(phone__icontains=search)
        )

    # Annotate with last transaction date and transaction count
    customers = customers.annotate(
        txn_count=Count('transactions', distinct=True),
        last_txn_date=Max('transactions__date'),
    ).order_by('name')

    # Build outstanding balance per customer (lightweight — just check credit txns)
    today = timezone.localdate()
    customer_rows = []
    for c in customers:
        credit_total = Transaction.objects.filter(
            business=business,
            recipient=c.name,
            payment_method='credit',
            type='Issue',
        ).aggregate(total=Count('id'))
        customer_rows.append({
            'customer': c,
            'txn_count': c.txn_count or 0,
            'last_seen': c.last_txn_date,
            'days_since': (today - c.last_txn_date).days if c.last_txn_date else None,
            'is_birthday_week': _is_birthday_this_week(c.dob, today),
        })

    return render(request, 'core/promo/customer_list.html', {
        'customer_rows': customer_rows,
        'search': search,
        'total': len(customer_rows),
        'is_owner': getattr(up, 'is_owner', False),
    })


def _is_birthday_this_week(dob, today):
    if not dob:
        return False
    this_year_bday = dob.replace(year=today.year)
    diff = (this_year_bday - today).days
    return 0 <= diff <= 7


# ── Customer quick-edit (phone / notes / dob) ─────────────────────────────────

@login_required
@require_POST
def customer_update(request, customer_id):
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner', False):
        return JsonResponse({'ok': False, 'error': 'Owner only.'}, status=403)
    try:
        c = Customer.objects.get(id=customer_id, business=up.business)
    except Customer.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Not found.'}, status=404)

    phone = request.POST.get('phone', c.phone).strip()
    notes = request.POST.get('notes', c.notes).strip()
    dob_raw = (request.POST.get('dob') or '').strip()
    dob = None
    if dob_raw:
        try:
            dob = datetime.date.fromisoformat(dob_raw)
        except ValueError:
            pass

    c.phone = phone
    c.notes = notes
    c.dob = dob
    c.save(update_fields=['phone', 'notes', 'dob'])
    return JsonResponse({'ok': True})


# ── Promo compose + send ──────────────────────────────────────────────────────

@login_required
def promo_compose(request):
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner', False):
        return redirect('home')

    business = up.business
    today = timezone.localdate()

    if request.method == 'POST':
        segment  = (request.POST.get('segment') or PromoMessage.SEGMENT_ALL).strip()
        channel  = (request.POST.get('channel') or PromoMessage.CHANNEL_SMS).strip()
        message  = (request.POST.get('message') or '').strip()
        subject  = (request.POST.get('subject') or '').strip()
        custom_phones_raw = (request.POST.get('custom_phones') or '').strip()

        if not message:
            return redirect('promo_compose')

        recipients = _build_recipient_list(business, segment, custom_phones_raw, today)

        sent = 0
        for phone, name, customer_id in recipients:
            personalised = message.replace('{name}', name or 'Mteja')
            if channel in (PromoMessage.CHANNEL_SMS, PromoMessage.CHANNEL_BOTH):
                normalized = normalize_ke_phone(phone) if phone else None
                if normalized:
                    try:
                        send_sms_notification(personalised, normalized)
                        sent += 1
                    except Exception:
                        pass
            if channel in (PromoMessage.CHANNEL_INAPP, PromoMessage.CHANNEL_BOTH):
                if customer_id:
                    try:
                        cust_obj = Customer.objects.get(id=customer_id)
                        Notification.objects.create(
                            business=business,
                            user=None,
                            message=f"📢 {personalised}",
                        )
                    except Customer.DoesNotExist:
                        pass
                    sent += 1

        PromoMessage.objects.create(
            business=business,
            sent_by=request.user,
            subject=subject,
            message=message,
            segment=segment,
            custom_phones=custom_phones_raw,
            channel=channel,
            recipient_count=sent,
        )
        return redirect('promo_history')

    # GET — render compose form with segment previews
    segment_counts = {
        'all': Customer.objects.filter(business=business).count(),
        'debtors': _count_debtors(business),
        'tab_customers': _count_tab_customers(business),
        'regulars': _count_regulars(business),
        'birthday': _count_birthday_week(business, today),
    }

    return render(request, 'core/promo/promo_compose.html', {
        'segment_counts': segment_counts,
        'is_owner': True,
    })


@login_required
def promo_history(request):
    up = _get_up(request)
    if not up or not getattr(up, 'is_owner', False):
        return redirect('home')
    promos = PromoMessage.objects.filter(business=up.business).select_related('sent_by')[:50]
    return render(request, 'core/promo/promo_history.html', {
        'promos': promos,
        'is_owner': True,
    })


# ── Segment builder helpers ───────────────────────────────────────────────────

def _build_recipient_list(business, segment, custom_phones_raw, today):
    """Return list of (phone, name, customer_id) tuples for the given segment."""
    if segment == PromoMessage.SEGMENT_CUSTOM:
        phones = [p.strip() for p in custom_phones_raw.split(',') if p.strip()]
        return [(p, '', None) for p in phones]

    if segment == PromoMessage.SEGMENT_ALL:
        qs = Customer.objects.filter(business=business).exclude(phone='')
    elif segment == PromoMessage.SEGMENT_DEBTORS:
        debtor_names = set(
            Transaction.objects.filter(
                business=business, type='Issue', payment_method='credit',
            ).values_list('recipient', flat=True).distinct()
        )
        qs = Customer.objects.filter(
            business=business, name__in=debtor_names,
        ).exclude(phone='')
    elif segment == PromoMessage.SEGMENT_TAB:
        tab_names = set(
            BarTab.objects.filter(business=business).values_list('customer_name', flat=True).distinct()
        )
        qs = Customer.objects.filter(
            business=business, name__in=tab_names,
        ).exclude(phone='')
    elif segment == PromoMessage.SEGMENT_REGULARS:
        regular_ids = (
            Transaction.objects.filter(business=business, type='Issue')
            .values('recipient')
            .annotate(cnt=Count('id'))
            .filter(cnt__gte=3)
            .values_list('recipient', flat=True)
        )
        qs = Customer.objects.filter(
            business=business, name__in=regular_ids,
        ).exclude(phone='')
    elif segment == PromoMessage.SEGMENT_BIRTHDAY:
        qs = Customer.objects.filter(
            business=business,
        ).exclude(phone='').exclude(dob__isnull=True)
        qs = [c for c in qs if _is_birthday_this_week(c.dob, today)]
        return [(c.phone, c.name, c.id) for c in qs]
    else:
        qs = Customer.objects.none()

    return [(c.phone, c.name, c.id) for c in qs]


def _count_debtors(business):
    debtor_names = set(
        Transaction.objects.filter(
            business=business, type='Issue', payment_method='credit',
        ).values_list('recipient', flat=True).distinct()
    )
    return Customer.objects.filter(
        business=business, name__in=debtor_names,
    ).exclude(phone='').count()


def _count_tab_customers(business):
    tab_names = set(
        BarTab.objects.filter(business=business).values_list('customer_name', flat=True).distinct()
    )
    return Customer.objects.filter(
        business=business, name__in=tab_names,
    ).exclude(phone='').count()


def _count_regulars(business):
    regular_ids = (
        Transaction.objects.filter(business=business, type='Issue')
        .values('recipient')
        .annotate(cnt=Count('id'))
        .filter(cnt__gte=3)
        .values_list('recipient', flat=True)
    )
    return Customer.objects.filter(
        business=business, name__in=regular_ids,
    ).exclude(phone='').count()


def _count_birthday_week(business, today):
    qs = Customer.objects.filter(
        business=business,
    ).exclude(phone='').exclude(dob__isnull=True)
    return sum(1 for c in qs if _is_birthday_this_week(c.dob, today))
