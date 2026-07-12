"""
Guided Stock Reconciliation views.

Flow:
  1. Owner/manager opens /stock/take/ → enters physical counts.
  2. On POST, StockTake + StockVarianceQuery rows are created for non-zero variances.
  3. The shift's staff member (if linked) is notified via SMS + in-app.
  4. Staff responds at /stock/variances/<id>/respond/.
  5. Owner reviews at /stock/variances/ and accepts (creates corrective Transaction) or dismisses.
  6. Dismissed variances set compliance_noted=True → appear on Haki contribution report.
"""

import json
import logging
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.models import (
    Item, ShiftStockCount, StockTake, StockVarianceQuery, Store, Transaction,
)
from core.notifications import (
    create_in_app_notification, normalize_ke_phone, send_sms_notification,
)
from core.views import get_user_profile, owner_or_manager_required

logger = logging.getLogger(__name__)


# ── Helper ────────────────────────────────────────────────────────────────────

def _notify_owner(business, title, message):
    """Send in-app + SMS notification to all owners of a business."""
    from accounts.models import UserProfile
    owners = UserProfile.objects.filter(business=business, role='owner').select_related('user')
    for op in owners:
        create_in_app_notification(op.user, title, message, notification_type='warning')
        if op.phone:
            send_sms_notification(message, normalize_ke_phone(op.phone))


# ── View 1: Start / submit a stock take ───────────────────────────────────────

@owner_or_manager_required
def start_stock_take(request):
    user_profile = get_user_profile(request)
    business = user_profile.business

    # Optional query params
    store_id  = request.GET.get('store') or request.POST.get('store')
    shift_id  = request.GET.get('shift') or request.POST.get('shift')

    from core.models import Shift
    linked_shift = None
    if shift_id:
        linked_shift = Shift.objects.filter(id=shift_id, business=business).first()

    scoped_store = None
    if store_id:
        scoped_store = Store.objects.filter(id=store_id, business=business).first()

    if request.method == 'POST':
        # Accept JSON body or form-encoded counts[]
        raw_counts = request.POST.get('counts')
        if raw_counts:
            try:
                counts = json.loads(raw_counts)
            except (json.JSONDecodeError, TypeError):
                counts = []
        else:
            counts = []

        if not counts:
            return JsonResponse({'ok': False, 'error': 'Hakuna hesabu zilizotumwa.'}, status=400)

        # Create the header
        stock_take = StockTake.objects.create(
            business=business,
            store=scoped_store,
            conducted_by=request.user,
            shift=linked_shift,
        )

        # Identify the staff member being queried (shift's staff, if linked)
        queried_staff = None
        if linked_shift and linked_shift.staff:
            from accounts.models import UserProfile
            queried_staff = UserProfile.objects.filter(
                user=linked_shift.staff, business=business
            ).first()

        variances_created = 0
        variance_items = []

        for row in counts:
            try:
                item_id      = int(row.get('item_id', 0))
                actual_count = Decimal(str(row.get('actual_count', 0)))
            except (TypeError, ValueError, InvalidOperation):
                continue

            item = Item.objects.filter(id=item_id, store__business=business).first()
            if item is None:
                continue

            book_balance = item.current_balance()

            # Write backward-compat ShiftStockCount when shift is linked
            if linked_shift:
                ShiftStockCount.objects.update_or_create(
                    shift=linked_shift,
                    item=item,
                    defaults={
                        'book_balance': book_balance,
                        'actual_count': actual_count,
                        'recorded_by':  request.user,
                    },
                )

            variance = actual_count - book_balance
            if variance == 0:
                continue

            direction = StockVarianceQuery.DECREASE if variance < 0 else StockVarianceQuery.INCREASE
            estimated_revenue = None
            if direction == StockVarianceQuery.DECREASE and item.selling_price:
                estimated_revenue = abs(variance) * item.selling_price

            svq = StockVarianceQuery.objects.create(
                stock_take=stock_take,
                item=item,
                item_name_cache=item.description,
                book_balance=book_balance,
                actual_count=actual_count,
                direction=direction,
                estimated_revenue=estimated_revenue,
                queried_staff=queried_staff,
            )
            variances_created += 1
            variance_items.append(
                f"{item.description}: {'−' if variance < 0 else '+'}{abs(variance):.2g} {item.unit}"
            )

        # Notifications
        if variances_created:
            items_summary = ', '.join(variance_items[:5])
            if len(variance_items) > 5:
                items_summary += f' ... (+{len(variance_items) - 5} zaidi)'

            conductor_name = (
                request.user.get_full_name() or request.user.username
            )
            owner_msg = (
                f"Hesabu ya stok na {conductor_name}: tofauti {variances_created} "
                f"imepatikana ({items_summary}). Angalia: /stock/variances/"
            )
            _notify_owner(business, f"📊 Tofauti za Stok ({variances_created})", owner_msg)

            # Notify queried staff
            if queried_staff and queried_staff.phone:
                staff_msg = (
                    f"Kuna tofauti {variances_created} za stok wakati wa zamu yako "
                    f"({items_summary}). Tafadhali eleza: jaribu ukurasa wa 'Variances' katika app."
                )
                create_in_app_notification(
                    queried_staff.user,
                    f"📊 Tofauti {variances_created} za Stok",
                    staff_msg,
                    notification_type='warning',
                )
                send_sms_notification(staff_msg, normalize_ke_phone(queried_staff.phone))

        is_quick = request.POST.get('quick') == '1'
        if is_quick:
            return JsonResponse({'ok': True, 'take_id': stock_take.id, 'variance_count': variances_created})
        return redirect('stock_take_detail', take_id=stock_take.id)

    # ── GET ──────────────────────────────────────────────────────────────────
    # Build item list scoped to the right store(s)
    items_qs = Item.objects.filter(
        store__business=business,
        is_produce=False,
    ).select_related('store').order_by('store__name', 'description')

    if scoped_store:
        items_qs = items_qs.filter(store=scoped_store)

    items_data = []
    for item in items_qs:
        items_data.append({
            'id':              item.id,
            'description':     item.description,
            'unit':            item.unit,
            'balance':         float(item.current_balance()),
            'store_name':      item.store.name if item.store else '',
            'volume_ml':       item.volume_ml,
            'selling_price':   float(item.selling_price) if item.selling_price else None,
            'material_number': item.material_no or '',
        })

    stores = Store.objects.filter(business=business).order_by('name')

    return render(request, 'core/stock_take_form.html', {
        'items_data':   json.dumps(items_data),
        'stores':       stores,
        'scoped_store': scoped_store,
        'linked_shift': linked_shift,
        'shift_id':     shift_id or '',
        'store_id':     store_id or '',
    })


# ── View 2: Stock take detail (post-submission summary) ───────────────────────

@login_required
def stock_take_detail(request, take_id):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect('home')
    business = user_profile.business

    stock_take = get_object_or_404(StockTake, id=take_id, business=business)

    # Access: the person who conducted OR owner/manager
    if (not user_profile.is_owner_or_manager
            and stock_take.conducted_by != request.user):
        return redirect('home')

    variances = stock_take.variances.select_related(
        'item', 'queried_staff__user', 'owner_action_by',
    ).order_by('-created_at')

    return render(request, 'core/stock_take_detail.html', {
        'stock_take': stock_take,
        'variances':  variances,
        'is_owner':   user_profile.is_owner_or_manager,
    })


# ── View 3: History list ──────────────────────────────────────────────────────

@owner_or_manager_required
def stock_take_history(request):
    user_profile = get_user_profile(request)
    business = user_profile.business

    takes = StockTake.objects.filter(business=business).select_related(
        'conducted_by', 'store', 'shift',
    ).prefetch_related('variances')[:50]

    takes_data = []
    for st in takes:
        total   = st.variances.count()
        pending = st.variances.filter(status=StockVarianceQuery.PENDING).count()
        takes_data.append({'take': st, 'total': total, 'pending': pending})

    return render(request, 'core/stock_take_history.html', {
        'takes_data': takes_data,
    })


# ── View 4: Owner review — all pending variances ──────────────────────────────

@owner_or_manager_required
def pending_variances(request):
    user_profile = get_user_profile(request)
    business = user_profile.business

    pending   = StockVarianceQuery.objects.filter(
        stock_take__business=business, status=StockVarianceQuery.PENDING,
    ).select_related('stock_take__conducted_by', 'item', 'queried_staff__user').order_by('created_at')

    responded = StockVarianceQuery.objects.filter(
        stock_take__business=business, status=StockVarianceQuery.RESPONDED,
    ).select_related('stock_take__conducted_by', 'item', 'queried_staff__user').order_by('responded_at')

    resolved  = StockVarianceQuery.objects.filter(
        stock_take__business=business, status=StockVarianceQuery.RESOLVED,
    ).select_related('stock_take__conducted_by', 'item', 'queried_staff__user', 'corrective_txn',
                     'owner_action_by').order_by('-owner_acted_at')[:30]

    return render(request, 'core/stock_variances_pending.html', {
        'pending':   pending,
        'responded': responded,
        'resolved':  resolved,
        'is_owner':  user_profile.is_owner_or_manager,
    })


# ── View 5: Staff response form ───────────────────────────────────────────────

@login_required
def respond_to_variance(request, var_id):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect('home')
    business = user_profile.business

    svq = get_object_or_404(StockVarianceQuery, id=var_id, stock_take__business=business)

    # Privacy gate: only the queried staff member or owner/manager
    is_queried = (
        svq.queried_staff is not None
        and svq.queried_staff.user == request.user
    )
    if not user_profile.is_owner_or_manager and not is_queried:
        return redirect('home')

    if svq.status == StockVarianceQuery.RESOLVED:
        return render(request, 'core/stock_variance_respond.html', {
            'svq': svq, 'already_resolved': True,
        })

    if request.method == 'POST':
        response_type     = request.POST.get('response_type', '').strip()
        response_customer = request.POST.get('response_customer', '').strip()
        response_note     = request.POST.get('response_note', '').strip()

        if not response_type:
            return render(request, 'core/stock_variance_respond.html', {
                'svq': svq, 'error': 'Tafadhali chagua aina ya jibu.',
                'response_choices': StockVarianceQuery.RESPONSE_CHOICES,
            })

        svq.response_type     = response_type
        svq.response_customer = response_customer
        svq.response_note     = response_note
        svq.responded_at      = timezone.now()
        svq.status            = StockVarianceQuery.RESPONDED
        svq.save(update_fields=[
            'response_type', 'response_customer', 'response_note',
            'responded_at', 'status',
        ])

        # Notify owner
        conductor_name = request.user.get_full_name() or request.user.username
        resp_label = dict(StockVarianceQuery.RESPONSE_CHOICES).get(response_type, response_type)
        _notify_owner(
            business,
            f"📊 Jibu la Tofauti: {svq.item_name_cache}",
            f"{conductor_name} amejibu tofauti ya {svq.item_name_cache}: {resp_label}.",
        )

        return render(request, 'core/stock_variance_respond.html', {
            'svq': svq, 'submitted': True,
        })

    # GET: show optional preset hint
    preset_hint = None
    if svq.item and svq.direction == StockVarianceQuery.DECREASE:
        from core.models import ItemPortionPreset
        presets = ItemPortionPreset.objects.filter(item=svq.item).order_by('price')
        if presets.exists() and svq.item.volume_ml:
            cheapest = presets.first()
            approx = int(abs(float(svq.variance)) / float(cheapest.quantity_consumed or 1))
            if approx > 0:
                preset_hint = (
                    f"Tofauti ya {abs(svq.variance):.2g} ≈ {approx} × "
                    f"{cheapest.label} (KES {cheapest.price} kila moja)"
                )

    return render(request, 'core/stock_variance_respond.html', {
        'svq':              svq,
        'response_choices': StockVarianceQuery.RESPONSE_CHOICES,
        'preset_hint':      preset_hint,
    })


# ── View 6: Owner review action (accept / dismiss) ────────────────────────────

@owner_or_manager_required
def review_variance(request, var_id):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)

    user_profile = get_user_profile(request)
    business = user_profile.business

    svq = get_object_or_404(StockVarianceQuery, id=var_id, stock_take__business=business)

    if svq.status == StockVarianceQuery.RESOLVED:
        return JsonResponse({'ok': False, 'error': 'Already resolved.'})

    action = request.POST.get('action', '')  # 'accept' or 'dismiss'

    if action == 'accept':
        corrective_txn = None
        try:
            if (svq.direction == StockVarianceQuery.DECREASE
                    and svq.response_type in (
                        StockVarianceQuery.RESP_CASH,
                        StockVarianceQuery.RESP_MPESA,
                        StockVarianceQuery.RESP_CREDIT,
                    )
                    and svq.item):
                corrective_txn = Transaction.objects.create(
                    business=business,
                    item=svq.item,
                    type='Issue',
                    qty=abs(svq.variance),
                    sale_amount=(svq.estimated_revenue if svq.estimated_revenue else None),
                    payment_method=svq.response_type,
                    recipient=svq.response_customer or '',
                    recorded_by=request.user,
                    date=svq.stock_take.taken_at.date(),
                )
            elif (svq.direction == StockVarianceQuery.INCREASE
                  and svq.response_type == StockVarianceQuery.RESP_RECEIPT
                  and svq.item):
                corrective_txn = Transaction.objects.create(
                    business=business,
                    item=svq.item,
                    type='Receipt',
                    qty=svq.variance,
                    payment_method='',
                    recorded_by=request.user,
                    date=svq.stock_take.taken_at.date(),
                )
        except Exception as exc:
            logger.exception("Error creating corrective transaction for variance %s", var_id)
            return JsonResponse({'ok': False, 'error': str(exc)})

        svq.owner_accepted  = True
        svq.owner_action_by = request.user
        svq.owner_acted_at  = timezone.now()
        svq.corrective_txn  = corrective_txn
        svq.status          = StockVarianceQuery.RESOLVED
        svq.save(update_fields=[
            'owner_accepted', 'owner_action_by', 'owner_acted_at',
            'corrective_txn', 'status',
        ])

        msg = 'Imekubaliwa'
        if corrective_txn:
            msg += f' — transaction ya {svq.item_name_cache} imeundwa.'
        return JsonResponse({'ok': True, 'message': msg})

    elif action == 'dismiss':
        svq.owner_accepted   = False
        svq.owner_action_by  = request.user
        svq.owner_acted_at   = timezone.now()
        svq.compliance_noted = True
        svq.status           = StockVarianceQuery.RESOLVED
        svq.save(update_fields=[
            'owner_accepted', 'owner_action_by', 'owner_acted_at',
            'compliance_noted', 'status',
        ])

        # Notify staff
        if svq.queried_staff:
            create_in_app_notification(
                svq.queried_staff.user,
                f"⚠️ Tofauti Imekataliwa: {svq.item_name_cache}",
                f"Mmiliki amekataa maelezo yako ya tofauti ya {svq.item_name_cache}. "
                f"Imerekodiwa kwenye rekodi yako ya utendaji.",
                notification_type='warning',
            )

        return JsonResponse({'ok': True, 'message': 'Imekataliwa na imerekodiwa kwenye rekodi ya utendaji.'})

    return JsonResponse({'ok': False, 'error': 'Invalid action.'})
