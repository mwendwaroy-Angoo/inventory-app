import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt

from .models import Receipt
from .notifications import normalize_ke_phone, send_email_notification, send_sms_notification
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

    search = request.GET.get('q', '').strip()

    qs = Receipt.objects.filter(
        business=user_profile.business,
        created_at__year=year,
        created_at__month=month,
    ).select_related('created_by')

    if search:
        qs = qs.filter(customer_name__icontains=search)

    # Kitchen-only staff see only kitchen receipts unless they also have bar access
    if not user_profile.is_owner and getattr(user_profile, 'is_kitchen_staff', False):
        if not getattr(user_profile, 'can_access_bar', False):
            qs = qs.filter(source='kitchen')

    receipts = qs.order_by('-created_at')

    # Build month options for the filter UI (current year, plus one back)
    import calendar as _cal
    month_options = [(m, _cal.month_abbr[m]) for m in range(1, 13)]

    return render(request, 'core/receipts_list.html', {
        'receipts':      receipts,
        'sel_month':     month,
        'sel_year':      year,
        'search':        search,
        'month_options': month_options,
        'cur_year':      now.year,
    })


def _get_live_tab_state(receipt):
    """Return (is_live, tab_status, lines, outstanding) for a tab-linked receipt.

    Returns ALL entries (paid and unpaid) so the customer can see what they've
    already paid (✓) and what is still pending (checkbox). Each line carries:
        entry_id  — set for unpaid entries, None for paid (pay section filters by this)
        is_paid   — bool flag
        is_kitchen — bool, for station icon rendering
    The returned ``outstanding`` total is the sum of UNPAID entries only.
    Returns is_live=False when no tab_id in meta or tab not found.
    """
    tab_id = receipt.meta.get('tab_id') if receipt.meta else None
    if not tab_id:
        return False, None, None, None
    try:
        from .models import BarTab as _BarTab
        tab = _BarTab.objects.get(id=tab_id, business=receipt.business)
        all_tab_ids = [tab_id] + list(receipt.meta.get('linked_tab_ids') or [])
        lines = []
        outstanding = 0.0
        for btab_id in all_tab_ids:
            try:
                btab = _BarTab.objects.get(id=btab_id, business=receipt.business)
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
                        outstanding += amt
                    lines.append({
                        'name': icon + e.description,
                        'qty': 1,
                        'subtotal': amt,
                        'entry_id': e.id if not e.is_paid else None,
                        'tab_id': btab_id,
                        'is_paid': e.is_paid,
                        'is_kitchen': is_kitchen,
                    })
            except _BarTab.DoesNotExist:
                pass
        # DEBT detection: SETTLED tab with unpaid balance means Geuza Deni was used.
        effective_status = tab.status
        if tab.status == 'SETTLED' and outstanding > 0:
            effective_status = 'DEBT'
        is_live = effective_status in ('OPEN', 'DEBT')
        return is_live, effective_status, lines, outstanding
    except Exception:
        return False, None, None, None


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

    return render(request, 'core/receipt_public.html', {
        'receipt':     receipt,
        'receipt_url': receipt_url,
        'is_live_tab': is_live_tab,
        'tab_status':  tab_status,
    })


def receipt_live_status(request, token):
    """AJAX polling endpoint for the live receipt page.

    Returns the current tab state as JSON so the client can update the DOM
    without a full page reload. No auth required — token is the secret.
    """
    receipt = get_object_or_404(Receipt, token=token)
    is_live, tab_status, lines, total = _get_live_tab_state(receipt)
    if lines is None:
        return JsonResponse({'is_live': False, 'tab_status': tab_status})
    return JsonResponse({
        'is_live':    is_live,
        'tab_status': tab_status,
        'lines':      lines,
        'total':      total,
    })


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
    tab_id = receipt.meta.get('tab_id') if receipt.meta else None
    if not tab_id:
        return JsonResponse({'error': 'not_a_tab'}, status=400)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    pay_type = data.get('type', 'stk')
    entry_ids = data.get('entry_ids') or []
    phone = (data.get('phone') or receipt.customer_phone or '').strip()

    business = receipt.business
    all_tab_ids = [tab_id] + list(receipt.meta.get('linked_tab_ids') or [])

    from .models import BarTabEntry
    if entry_ids:
        entries_qs = BarTabEntry.objects.filter(
            id__in=entry_ids,
            tab__id__in=all_tab_ids,
            is_paid=False,
        )
    else:
        entries_qs = BarTabEntry.objects.filter(tab__id__in=all_tab_ids, is_paid=False)

    entries_list = list(entries_qs.select_related('transaction__item__store'))
    amount = int(sum(float(e.amount) for e in entries_list))
    selected_ids = [e.id for e in entries_list]

    if amount < 1:
        return JsonResponse({'error': 'nothing_to_pay'}, status=400)

    # Station-aware M-Pesa routing: if all selected items are from one store
    # with its own till/paybill, route to that store's config instead of business level.
    from .mpesa import resolve_mpesa_config
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

    # STK Push
    if not phone:
        return JsonResponse({'error': 'phone_required'}, status=400)

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
            source=cfg.get('source', 'bar'),
            amount=amount,
            method='mpesa',
            status='pending',
            phone=phone_fmt,
            checkout_request_id=result.get('CheckoutRequestID', ''),
            merchant_request_id=result.get('MerchantRequestID', ''),
            tab_entry_ids=selected_ids,
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
