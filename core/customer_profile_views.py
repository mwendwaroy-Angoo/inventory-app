"""
Customer journey, public ledger, and the all-staff customer search.

2026-08-23 live request (Roy), items 1, 2, 5 and 6:
  - a customer's full transaction history (date, time, item, served by,
    recorded by) — reachable by ANY staff member, not just owner/manager.
    Roy's own reason for widening it: "customers have been asking for one
    simple thing, 'can you search for me in your system?' and I have never
    been able to do that."
  - a PUBLIC, token-addressed ledger the customer reaches from the
    "Historia yako" link in their own reminder SMS.
  - one search endpoint, surfaced both on the dashboard and across the bar
    interface (the map called these items 5 and 6 separately; they are the
    same feature at two reach levels, so there is one endpoint).

Access note, deliberate and recorded: the journey page is open to every
logged-in staff member of the business, including debt balance and history.
That is wider than _debt_scope()'s station partitioning, which restricts a
kitchen-only staffer to kitchen debts on the debt tracker itself. Roy made
that call explicitly. Station scoping still governs who can ACT on debt
(record payments, convert tabs, write off) — this widening is READ-ONLY.
"""
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.views.decorators.cache import never_cache

from .models import Customer
from .customer_profile import (
    customer_payment_history, customer_summary, customer_transaction_history,
)
from .views import get_user_profile

logger = logging.getLogger(__name__)


@login_required
def customer_journey(request, customer_id):
    """Staff-facing full customer journey — every purchase with who served it
    and who recorded it, plus every debt payment and what it went towards."""
    up = get_user_profile(request)
    if not up or not up.business:
        return redirect('home')
    business = up.business
    customer = get_object_or_404(Customer, id=customer_id, business=business)

    history = customer_transaction_history(business, customer)
    payments = customer_payment_history(business, customer)
    summary = customer_summary(business, customer, history=history)

    from .debt_views import _get_customer_debt_data
    try:
        debt = _get_customer_debt_data(customer, business, scope='all')
    except Exception:
        logger.exception('Debt data failed for customer %s journey', customer.id)
        debt = None

    return render(request, 'core/customer_journey.html', {
        'customer': customer,
        'history': history,
        'payments': payments,
        'summary': summary,
        'debt': debt,
        'is_owner': up.is_owner_or_manager,
    })


@login_required
@require_POST
def update_customer_phone(request, customer_id):
    """Save a customer's phone number.

    2026-08-23: the "📱 Send Reminder" button on the debt profile has always
    been gated `{% if outstanding > 0 and customer.phone %}` — but nothing
    anywhere on that page could SET a phone, so for any customer without one
    the button was permanently invisible and the whole reminder feature
    looked like it did not exist. This is the missing half.

    Open to any staff member: taking down a customer's number at the counter
    is an everyday act, not an owner decision, and gating it behind the owner
    is exactly what kept it from ever being filled in.
    """
    up = get_user_profile(request)
    if not up or not up.business:
        return redirect('home')
    customer = get_object_or_404(Customer, id=customer_id, business=up.business)

    phone = (request.POST.get('phone') or '').strip()
    from core.notifications import normalize_ke_phone
    if phone and not normalize_ke_phone(phone):
        messages.error(request, f'Nambari ya simu si sahihi: {phone}')
    else:
        customer.phone = phone
        customer.save(update_fields=['phone'])
        messages.success(
            request,
            f'Nambari ya {customer.name} imehifadhiwa.' if phone
            else f'Nambari ya {customer.name} imeondolewa.',
        )
    return redirect(request.POST.get('next') or 'customer_debt_profile', customer_id=customer.id)


@login_required
def customer_lookup_api(request):
    """AJAX customer search for ALL staff — name substring, with each match's
    live standing so the person at the counter can answer "what do I owe?"
    without leaving the screen they are on.

    Deliberately separate from debt_views.customer_search_api, which is the
    owner-only merge picker and returns a different (deliberately minimal)
    shape for a different job.
    """
    up = get_user_profile(request)
    if not up or not up.business:
        return JsonResponse({'results': []})
    q = (request.GET.get('q') or '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})

    from .debt_views import _get_customer_debt_data
    customers = Customer.objects.filter(
        business=up.business, name__icontains=q,
    ).order_by('name')[:8]

    results = []
    for c in customers:
        outstanding = 0.0
        score_label = ''
        try:
            data = _get_customer_debt_data(c, up.business, scope='all')
            outstanding = data['outstanding']
            score_label = str(data.get('score_label') or '')
        except Exception:
            logger.exception('Debt lookup failed for customer %s', c.id)
        results.append({
            'id': c.id,
            'name': c.name,
            'phone': c.phone or '',
            'outstanding': outstanding,
            'score_label': score_label,
            'is_defaulter': bool(c.is_defaulter),
            'is_owner_alias': bool(c.is_owner_alias),
            'journey_url': f'/customers/{c.id}/journey/',
        })
    return JsonResponse({'results': results})


@never_cache
def customer_ledger_public(request, token):
    """The customer's OWN ledger, reached from the link in their reminder SMS.

    No login — the unguessable token is the proof of identity, the same
    security model every public receipt page in this app already uses. Shows
    their itemised purchase history and every payment they have made, so the
    figure being quoted at them is checkable rather than something they have
    to take on trust.
    """
    token = (token or '').strip()
    if not token:
        return render(request, 'core/customer_ledger_public.html',
                      {'not_found': True}, status=404)
    customer = Customer.objects.filter(ledger_token=token).select_related('business').first()
    if not customer:
        return render(request, 'core/customer_ledger_public.html',
                      {'not_found': True}, status=404)

    business = customer.business
    history = customer_transaction_history(business, customer)
    payments = customer_payment_history(business, customer)
    summary = customer_summary(business, customer, history=history)

    from .debt_views import _get_customer_debt_data
    try:
        debt = _get_customer_debt_data(customer, business, scope='all')
    except Exception:
        logger.exception('Debt data failed for public ledger, customer %s', customer.id)
        debt = None

    return render(request, 'core/customer_ledger_public.html', {
        'customer': customer,
        'business': business,
        'history': history,
        'payments': payments,
        'summary': summary,
        'debt': debt,
    })
