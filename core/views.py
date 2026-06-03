import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.db.models import Q, Sum
from django.views.decorators.http import require_POST
from django.utils.translation import gettext as _
from .models import (
    Item,
    Transaction,
    Store,
    Customer,
    PurchaseOrder,
    PurchaseOrderLine,
    GoodsReceipt,
    GoodsReceiptLine,
)
from .forms import (
    ItemForm,
    PurchaseOrderForm,
    PurchaseOrderLineForm,
    PurchaseOrderLineFormSet,
    GoodsReceiptForm,
    GoodsReceiptLineFormSet,
)
from core.forecast_engine import run_ets, run_regression
import openpyxl
from datetime import date, timedelta
import json
import os

# ── HELPERS ──────────────────────────────────────────────────────────────────


def get_user_profile(request):
    try:
        return request.user.userprofile
    except Exception:
        return None


def health_check(request):
    """Lightweight health-check endpoint for Render's preboot / load balancer."""
    from django.db import connection

    try:
        connection.ensure_connection()
        return HttpResponse("ok", content_type="text/plain", status=200)
    except Exception:
        return HttpResponse("error", content_type="text/plain", status=503)


def manifest_json(request):
    """Serve the Web App Manifest at /manifest.json with proper content type."""
    from django.conf import settings
    import json

    manifest_path = settings.BASE_DIR / "static" / "manifest.json"
    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
        return JsonResponse(manifest, content_type="application/manifest+json")
    except (FileNotFoundError, json.JSONDecodeError):
        return JsonResponse({}, content_type="application/manifest+json")


def service_worker(request):
    """Serve the Service Worker at /sw.js with proper scope headers."""
    from django.conf import settings

    sw_path = settings.BASE_DIR / "static" / "sw.js"
    try:
        with open(sw_path, "r") as f:
            content = f.read()
        response = HttpResponse(content, content_type="application/javascript")
        response["Service-Worker-Allowed"] = "/"
        response["Cache-Control"] = "no-cache"
        return response
    except FileNotFoundError:
        return HttpResponse("", content_type="application/javascript")


def offline(request):
    return render(request, "offline.html")


def owner_required(view_func):
    from functools import wraps

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        try:
            if not request.user.userprofile.is_owner:
                messages.error(request, _("Only business owners can access this page."))
                return redirect("stock_list")
        except Exception:
            return redirect("home")
        return view_func(request, *args, **kwargs)

    return wrapper


# ── HOME ─────────────────────────────────────────────────────────────────────


def home(request):
    context = {"today": timezone.now().strftime("%B %d, %Y")}

    if request.user.is_authenticated:
        profile = getattr(request.user, "userprofile", None)
        if profile and profile.role == "rider":
            return redirect("rider_dashboard")
        if profile and profile.role == "supplier":
            return redirect("supplier_dashboard")

        try:
            user_profile = request.user.userprofile
            business = user_profile.business
            all_items = Item.objects.filter(business=business)
            reorder_items = [item for item in all_items if item.needs_reorder()]
            low_stock_count = len(
                [
                    item
                    for item in all_items
                    if item.current_balance() <= item.reorder_level
                ]
            )
            reorder_count = len(reorder_items)

            context.update(
                {
                    "total_items": all_items.count(),
                    "low_stock_count": low_stock_count,
                    "reorder_count": reorder_count,
                    "reorder_items": sorted(
                        reorder_items, key=lambda x: x.current_balance()
                    )[:20],
                }
            )

            # Items received in last 7 days with no cost price set (owner dashboard alert)
            try:
                from datetime import timedelta as _td
                seven_days_ago = timezone.now().date() - _td(days=7)
                recent_receipts_no_cost = Transaction.objects.filter(
                    business=business,
                    type='Receipt',
                    date__gte=seven_days_ago,
                    item__cost_price__isnull=True,
                ).values('item__description', 'item__id').distinct()
                context['items_missing_cost_price'] = list(recent_receipts_no_cost)
                context['missing_cost_price_count'] = len(context['items_missing_cost_price'])
            except Exception:
                context['items_missing_cost_price'] = []
                context['missing_cost_price_count'] = 0

            # Revenue targets progress for dashboard widget
            try:
                from core.models import RevenueTarget
                from datetime import date as _date
                _today = _date.today()
                _week_start = _today - timedelta(days=_today.weekday())
                _month_start = _today.replace(day=1)

                def _period_rev(start, end):
                    txns = Transaction.objects.filter(
                        business=business, type='Issue',
                        date__gte=start, date__lte=end,
                    ).select_related('item')
                    return sum(t.revenue() for t in txns)

                def _get_target(ttype):
                    t = RevenueTarget.objects.filter(
                        business=business, target_type=ttype, store__isnull=True
                    ).first()
                    return float(t.amount) if t else 0

                def _build_target_data(actual, target):
                    actual = round(float(actual), 2)
                    target = float(target)
                    if target > 0:
                        pct = min(100, round((actual / target) * 100, 1))
                    else:
                        pct = 0
                    if pct >= 100:
                        color = '#6ee7b7'
                    elif pct >= 50:
                        color = '#fbbf24'
                    else:
                        color = '#f87171'
                    return {'actual': actual, 'target': target, 'pct': pct, 'color': color}

                context['revenue_targets'] = {
                    'daily':   _build_target_data(_period_rev(_today, _today),       _get_target('daily')),
                    'weekly':  _build_target_data(_period_rev(_week_start, _today),  _get_target('weekly')),
                    'monthly': _build_target_data(_period_rev(_month_start, _today), _get_target('monthly')),
                }
            except Exception:
                context['revenue_targets'] = None

        except Exception:
            context["error"] = _("Profile not found. Please contact support.")
    else:
        context["guest"] = True
        context["services"] = [
            (
                "📦",
                _("Inventory Management"),
                _(
                    "Track stock levels, costs, and reorder points in real time. Never run out of stock again."
                ),
            ),
            (
                "🛒",
                _("Online Marketplace"),
                _(
                    "Your own storefront where customers browse and order directly. No middleman."
                ),
            ),
            (
                "💳",
                _("M-Pesa Payments"),
                _(
                    "Accept payments via Lipa Na M-Pesa. Instant STK Push to your customers' phones."
                ),
            ),
            (
                "📱",
                _("USSD Access"),
                _(
                    "Record sales and check stock via USSD. Works on any phone, no internet needed."
                ),
            ),
            (
                "📊",
                _("Analytics Dashboard"),
                _(
                    "See your top products, revenue trends, and profit margins at a glance."
                ),
            ),
            (
                "👥",
                _("Staff Management"),
                _(
                    "Add staff, assign roles, and get notified when they log in or record transactions."
                ),
            ),
        ]
        context["faqs"] = [
            (
                _("Is Duka Mwecheche free?"),
                _(
                    "Yes! The platform is completely free for all businesses. You only pay standard M-Pesa transaction fees when accepting payments."
                ),
            ),
            (
                _("Do I need a smartphone?"),
                _(
                    "No. You can manage your stock via USSD on any basic phone. The web app works on smartphones and computers too."
                ),
            ),
            (
                _("How do customers find my shop?"),
                _(
                    "Once you register and add items with prices, your business appears on the Marketplace. Customers can search by location and product."
                ),
            ),
            (
                _("Is my data safe?"),
                _(
                    "Absolutely. Your data is stored securely on cloud servers with regular backups. Only you and your staff can access your business data."
                ),
            ),
            (
                _("Can I accept M-Pesa payments?"),
                _(
                    "Yes. We integrate with Safaricom's Daraja API. You'll need a Till or Paybill number from Safaricom to receive funds directly."
                ),
            ),
            (
                _("How do I add staff?"),
                _(
                    "Go to Manage -> Staff -> Add Staff. Staff members can record transactions but cannot access business settings or financial reports."
                ),
            ),
        ]

    return render(request, "core/home.html", context)


# ── STOCK LIST ────────────────────────────────────────────────────────────────


@login_required
def stock_list(request):
    user_profile = get_user_profile(request)
    if not user_profile:
        messages.error(request, _("No business profile found."))
        return redirect("home")

    stores = Store.objects.filter(business=user_profile.business)
    selected_store_id = request.GET.get("store")
    status_filter = request.GET.get("status")

    items = Item.objects.filter(store__business=user_profile.business).order_by(
        "material_no"
    )

    if selected_store_id:
        try:
            selected_store_id = int(selected_store_id)
            items = items.filter(store_id=selected_store_id)
        except (ValueError, TypeError):
            pass

    all_items = list(items)

    if status_filter == "low_stock":
        all_items = [i for i in all_items if i.current_balance() <= i.reorder_level]
    elif status_filter == "reorder":
        all_items = [i for i in all_items if i.needs_reorder()]

    context = {
        "items": all_items,
        "stores": stores,
        "selected_store": selected_store_id if selected_store_id else None,
        "status_filter": status_filter,
        "today": timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, "core/stock_list.html", context)


# ── TRANSACTIONS ──────────────────────────────────────────────────────────────


@login_required
def add_transaction(request):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect("home")

    stores = Store.objects.filter(business=user_profile.business)
    customers = Customer.objects.filter(business=user_profile.business)

    if request.method == "POST":
        item_id = request.POST["item"]
        trans_type = request.POST["type"]
        quantity = int(request.POST["quantity"])
        invoice_no = request.POST.get("invoice_no", "")
        recipient = request.POST.get("recipient", "")

        new_customer_name = request.POST.get("new_customer_name", "").strip()
        if new_customer_name and trans_type == "Issue":
            customer, _created = Customer.objects.get_or_create(
                business=user_profile.business,
                name=new_customer_name,
                defaults={"phone": request.POST.get("new_customer_phone", "")},
            )
            recipient = customer.name

        item = get_object_or_404(Item, id=item_id)

        # ── RESTRICTED ITEM CHECK ─────────────────────────────────────────────
        can_override = getattr(user_profile, 'can_override_restrictions', False)
        if trans_type == 'Issue' and item.is_restricted and not user_profile.is_owner and not can_override:
            reserved = item.restricted_quantity or 0
            balance_after = item.current_balance() - quantity
            needs_approval = reserved == 0 or balance_after < reserved
            if needs_approval:
                from core.restricted_items_views import _create_approval_request
                approval = _create_approval_request(
                    request, item, user_profile,
                    quantity=quantity,
                    recipient=recipient,
                    invoice_no=invoice_no,
                    payment_method=request.POST.get('payment_method', 'cash'),
                )
                return render(request, 'core/sale_approval_pending.html', {
                    'approval': approval,
                    'item': item,
                })
            # else: sale is within freely-sellable range — falls through below
        # ─────────────────────────────────────────────────────────────────────

        if trans_type == "Issue":
            if item.current_balance() < quantity:
                messages.error(
                    request,
                    _(
                        "Not enough stock for %(item_description)s. Available: %(available)s %(unit)s, requested: %(requested)s."
                    )
                    % {
                        "item_description": item.description,
                        "available": item.current_balance(),
                        "unit": item.unit,
                        "requested": quantity,
                    },
                )
                return redirect("add_transaction")
            quantity = -quantity

        transaction = Transaction.objects.create(
            item=item,
            type=trans_type,
            qty=quantity,
            recipient=recipient,
            invoice_no=invoice_no,
            business=user_profile.business,
            payment_method=(
                request.POST.get("payment_method", "cash")
                if trans_type == "Issue"
                else ""
            ),
        )

        # ── COST PRICE UPDATE (Receipt only) ──────────────────────────────
        # When receiving stock, the delivered price may differ from the stored
        # cost price. A delivery fee can also be entered — in that case we
        # calculate the landed cost per unit and use that instead.
        #
        # Landed cost per unit = (qty × unit_price + delivery_fee) / qty
        #
        # If the owner ticked "update cost price", Item.cost_price is updated
        # to the landed cost per unit (or just the unit price if no delivery fee).
        if trans_type == "Receipt" and (user_profile.is_owner or getattr(user_profile, 'can_input_cost_price', False)):
            new_cost_price_raw = request.POST.get("new_cost_price", "").strip()
            delivery_fee_raw = request.POST.get("delivery_fee", "0").strip()
            update_cost_price = request.POST.get("update_cost_price") == "on"

            if update_cost_price and new_cost_price_raw:
                try:
                    from decimal import Decimal

                    unit_price = Decimal(new_cost_price_raw)
                    delivery_fee = (
                        Decimal(delivery_fee_raw) if delivery_fee_raw else Decimal("0")
                    )
                    qty_decimal = Decimal(
                        str(abs(quantity))
                    )  # quantity is already negative for issues

                    # Calculate landed cost per unit if there is a delivery fee
                    if delivery_fee > 0 and qty_decimal > 0:
                        landed_cost = (
                            (unit_price * qty_decimal) + delivery_fee
                        ) / qty_decimal
                    else:
                        landed_cost = unit_price

                    old_cost = item.cost_price

                    if landed_cost != old_cost:
                        item.cost_price = landed_cost
                        item.save(update_fields=["cost_price"])

                        if delivery_fee > 0:
                            messages.info(
                                request,
                                _(
                                    "Cost price for %(item)s updated to landed cost KES %(new)s/%(unit)s "
                                    "(KES %(unit_price)s unit + KES %(fee)s delivery ÷ %(qty)s %(unit)s)."
                                )
                                % {
                                    "item": item.description,
                                    "new": f"{landed_cost:,.2f}",
                                    "unit": item.unit,
                                    "unit_price": f"{unit_price:,.2f}",
                                    "fee": f"{delivery_fee:,.2f}",
                                    "qty": f"{qty_decimal:,.0f}",
                                },
                            )
                        else:
                            messages.info(
                                request,
                                _(
                                    "Cost price for %(item)s updated from KES %(old)s to KES %(new)s."
                                )
                                % {
                                    "item": item.description,
                                    "old": f"{old_cost:,.2f}" if old_cost else "—",
                                    "new": f"{landed_cost:,.2f}",
                                },
                            )
                except Exception:
                    pass  # Never block transaction recording due to cost price update failure
        # ─────────────────────────────────────────────────────────────────

        # Notify owner when staff records a receipt (cost price may need updating)
        if trans_type == 'Receipt' and not user_profile.is_owner:
            staff_name = request.user.get_full_name() or request.user.username
            cost_str = f'KES {item.cost_price}' if item.cost_price else 'not set'
            notif_msg = (
                f'{staff_name} received {abs(quantity)} {item.unit} of {item.description}. '
                f'Current cost price: {cost_str}. Update in Manage Items if needed.'
            )
            sms_msg = (
                f'COST PRICE NEEDED: {staff_name} received {abs(quantity)} {item.unit} '
                f'of {item.description}. Cost price is {cost_str}. '
                f'Log in to Duka Mwecheche to update it.'
            )
            email_subject = f'Cost price check needed — {item.description} | Duka Mwecheche'
            email_html = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;">
        <h2 style="color:#c9a84c;">📦 Receipt Recorded — Cost Price Needed</h2>
        <p><strong>{staff_name}</strong> has received stock:</p>
        <div style="background:#f5f5f5;padding:1rem;border-radius:8px;margin:1rem 0;">
            <strong style="font-size:1.1rem;">{item.description}</strong><br>
            Quantity received: {abs(quantity)} {item.unit}<br>
            Current cost price: {cost_str}<br>
            Store: {item.store.name if item.store else 'N/A'}
        </div>
        <p>Please <a href="https://www.dukamwecheche.co.ke/manage/items/">update the cost price</a>
        to keep your profit margin calculations accurate.</p>
        <p style="color:#888;font-size:0.85rem;">— Duka Mwecheche</p>
    </div>
    """

            owner_profiles = business.users.filter(role='owner')
            for op in owner_profiles:
                # In-app notification
                try:
                    Notification.objects.create(
                        user=op.user,
                        title='Receipt recorded — cost price check needed',
                        message=notif_msg,
                        notification_type='info',
                    )
                except Exception:
                    pass

                # SMS
                try:
                    from core.notifications import send_sms_notification, normalize_ke_phone
                    owner_phone = getattr(op, 'phone', '') or business.phone or ''
                    if owner_phone:
                        phone = normalize_ke_phone(owner_phone)
                        if phone:
                            send_sms_notification(sms_msg, phone)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error('Cost price SMS failed: %s', e)

                # Email
                try:
                    from core.notifications import send_email_notification
                    if op.user.email:
                        send_email_notification(op.user.email, email_subject, email_html)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error('Cost price email failed: %s', e)

        # ── YIELD: auto-create Wastage transaction for yield items ────────
        # When receiving a yield item (e.g. whole goat → sellable cuts),
        # the usable portion is qty × yield_factor. The remainder is wastage
        # and must be deducted from stock so the balance reflects usable qty.
        if trans_type == 'Receipt' and item.is_yield_item and item.yield_factor:
            received_qty = abs(quantity)  # quantity is positive for receipts
            wastage_qty = int(round(received_qty * (1 - float(item.yield_factor))))
            if wastage_qty > 0:
                Transaction.objects.create(
                    item=item,
                    type='Wastage',
                    qty=-wastage_qty,  # negative to reduce stock
                    recipient='',
                    invoice_no=invoice_no,
                    business=user_profile.business,
                )
                usable_qty = received_qty - wastage_qty
                messages.info(
                    request,
                    _(
                        'Yield applied: %(usable)s %(unit)s usable, '
                        '%(wastage)s %(unit)s wastage recorded (%(pct)s%% yield).'
                    ) % {
                        'usable': usable_qty,
                        'unit': item.unit,
                        'wastage': wastage_qty,
                        'pct': int(float(item.yield_factor) * 100),
                    },
                )
        # ─────────────────────────────────────────────────────────────────

        # Count today's transactions for SMS/WhatsApp decision
        daily_count = Transaction.objects.filter(
            business=user_profile.business, date=date.today()
        ).count()

        # Send notifications in a background thread — never block the HTTP response
        try:
            from .notifications import notify_transaction_async

            notify_transaction_async(
                transaction.id,
                user_profile.business.id,
                daily_count,
                user_id=request.user.id,
            )
        except Exception:
            pass

        messages.success(
            request,
            _(
                "%(quantity)s %(unit)s of %(item_description)s recorded as %(transaction_type)s."
            )
            % {
                "quantity": abs(quantity),
                "unit": item.unit,
                "item_description": item.description,
                "transaction_type": trans_type.lower(),
            },
        )
        return redirect("add_transaction")

    items = Item.objects.filter(store__business=user_profile.business).order_by(
        "material_no"
    )
    restricted_items_data = {}
    if not user_profile.is_owner:
        restricted_qs = Item.objects.filter(
            store__business=user_profile.business,
            is_restricted=True,
        ).values('id', 'restricted_quantity')
        for r in restricted_qs:
            restricted_items_data[r['id']] = r['restricted_quantity']

    is_owner = user_profile.is_owner
    can_input_cost = is_owner or getattr(user_profile, 'can_input_cost_price', False)
    context = {
        "items": items,
        "stores": stores,
        "customers": customers,
        "today": timezone.now().strftime("%B %d, %Y"),
        "restricted_item_ids": list(restricted_items_data.keys()),
        "restricted_items_data": restricted_items_data,
        "is_owner": is_owner,
        "can_input_cost_price": can_input_cost,
        "show_cost_price_input_only": (not is_owner) and can_input_cost,
    }
    return render(request, "core/add_transaction.html", context)


@login_required
def transaction_history(request):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect("home")

    transactions = (
        Transaction.objects.filter(item__store__business=user_profile.business)
        .select_related("item", "item__store")
        .order_by("-date")
    )

    context = {
        "transactions": transactions,
        "today": timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, "core/transaction_history.html", context)


@login_required
def export_transactions_excel(request):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect("home")

    transactions = (
        Transaction.objects.filter(item__store__business=user_profile.business)
        .select_related("item", "item__store")
        .order_by("-date")
    )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Transaction History"
    ws.append(
        [
            "Date",
            "Item",
            "Material No",
            "Store",
            "Type",
            "Qty",
            "Recipient",
            "Invoice No",
            "Payment Method",
        ]
    )

    for t in transactions:
        ws.append(
            [
                str(t.date),
                t.item.description,
                t.item.material_no,
                t.item.store.name,
                t.type,
                t.qty,
                t.recipient or "—",
                t.invoice_no or "—",
                t.get_payment_method_display() if t.payment_method else "Cash",
            ]
        )

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = "attachment; filename=transaction_history.xlsx"
    wb.save(response)
    return response


# ── ITEM DETAIL ───────────────────────────────────────────────────────────────


@login_required
def item_detail(request, item_id):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect("home")

    item = get_object_or_404(Item, id=item_id, store__business=user_profile.business)
    transactions = item.transactions.all().order_by("-date")
    context = {
        "item": item,
        "transactions": transactions,
        "today": timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, "core/item_detail.html", context)


@login_required
@owner_required
def create_po_from_item(request, item_id):
    """Quick action: create a draft Purchase Order for the item using recommended qty."""
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect("home")

    item = get_object_or_404(Item, id=item_id, store__business=user_profile.business)
    try:
        qty = item.recommended_order_qty()
    except Exception:
        qty = 0

    if not qty or qty <= 0:
        messages.info(request, _("No order recommended for this item."))
        return redirect("item_detail", item_id=item.id)

    po = PurchaseOrder.objects.create(
        business=user_profile.business,
        status="draft",
        created_by=request.user,
    )
    PurchaseOrderLine.objects.create(
        po=po,
        item=item,
        quantity_ordered=qty,
        unit_price=item.cost_price or 0,
    )
    messages.success(
        request,
        _("Draft purchase order created with %(qty)s units of %(item)s.")
        % {"qty": qty, "item": item.description},
    )
    return redirect("stock_list")


@login_required
@owner_required
def purchase_orders_list(request):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect("home")
    pos = (
        PurchaseOrder.objects.filter(business=user_profile.business)
        .order_by("-created_at")
        .prefetch_related("lines__item")
    )
    context = {
        "purchase_orders": pos,
        "today": timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, "core/purchase_order_list.html", context)


@login_required
@owner_required
def purchase_order_detail(request, po_id):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect("home")
    po = get_object_or_404(PurchaseOrder, id=po_id, business=user_profile.business)
    context = {
        "po": po,
        "today": timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, "core/purchase_order_detail.html", context)


@login_required
@owner_required
def purchase_order_create(request):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect("home")

    if request.method == "POST":
        form = PurchaseOrderForm(request.POST)
        temp_po = PurchaseOrder(business=user_profile.business)
        formset = PurchaseOrderLineFormSet(request.POST, instance=temp_po)
        if form.is_valid() and formset.is_valid():
            po = form.save(commit=False)
            po.business = user_profile.business
            po.created_by = request.user
            po.save()
            formset.instance = po
            lines = formset.save(commit=False)
            for line in lines:
                if line.item and line.item.business != user_profile.business:
                    continue
                line.save()
            messages.success(request, _("Purchase order created."))
            return redirect("purchase_order_detail", po.id)
    else:
        form = PurchaseOrderForm()
        temp_po = PurchaseOrder(business=user_profile.business)
        formset = PurchaseOrderLineFormSet(instance=temp_po)

    for f in formset.forms:
        if "item" in f.fields:
            f.fields["item"].queryset = Item.objects.filter(
                business=user_profile.business
            )

    context = {
        "form": form,
        "formset": formset,
        "today": timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, "core/purchase_order_form.html", context)


@login_required
def item_recommendation(request, item_id):
    """API: return recommended order qty and related metrics for an item."""
    user_profile = get_user_profile(request)
    if not user_profile:
        return JsonResponse({"error": "unauthorized"}, status=403)

    try:
        item = get_object_or_404(
            Item, id=item_id, store__business=user_profile.business
        )
    except Exception:
        return JsonResponse({"error": "not_found_or_unauthorized"}, status=404)

    try:
        recommended = item.recommended_order_qty()
    except Exception:
        recommended = 0

    data = {
        "recommended_qty": int(recommended or 0),
        "on_order": int(item.on_order() or 0),
        "current_balance": int(item.current_balance() or 0),
        "reorder_point": int(item.reorder_point() or 0),
        "target_stock": int(item.target_stock() or 0),
    }
    return JsonResponse(data)


@login_required
def item_search(request):
    """Simple search endpoint for items. Returns JSON list."""
    user_profile = get_user_profile(request)
    if not user_profile:
        return JsonResponse({"results": []})

    q = request.GET.get("q", "").strip()
    items_qs = Item.objects.filter(business=user_profile.business)
    if q:
        items_qs = items_qs.filter(
            Q(material_no__icontains=q) | Q(description__icontains=q)
        )
    items_qs = items_qs.order_by("material_no")[:30]
    results = []
    for it in items_qs:
        results.append(
            {
                "id": it.id,
                "material_no": it.material_no,
                "description": it.description,
                "cost_price": float(it.cost_price) if it.cost_price else None,
                "selling_price": float(it.selling_price) if it.selling_price else None,
                "unit": it.unit,
            }
        )
    return JsonResponse({"results": results})


@login_required
def item_cost_price(request, item_id):
    """
    AJAX endpoint: return cost price and key fields for a single item.
    Used by the Add Transaction form to show the cost price confirmation
    section when transaction type = Receipt.

    GET /core/items/<item_id>/cost-price/
    Returns: { cost_price, description, unit, selling_price }
    """
    user_profile = get_user_profile(request)
    if not user_profile:
        return JsonResponse({"error": "unauthorized"}, status=403)

    item = get_object_or_404(Item, id=item_id, store__business=user_profile.business)

    return JsonResponse(
        {
            "cost_price": float(item.cost_price) if item.cost_price else None,
            "description": item.description,
            "unit": item.unit,
            "selling_price": float(item.selling_price) if item.selling_price else None,
            "is_yield_item": item.is_yield_item,
            "yield_factor": float(item.yield_factor) if item.yield_factor else None,
        }
    )


@login_required
@owner_required
def purchase_order_edit(request, po_id):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect("home")
    po = get_object_or_404(PurchaseOrder, id=po_id, business=user_profile.business)

    if request.method == "POST":
        form = PurchaseOrderForm(request.POST, instance=po)
        formset = PurchaseOrderLineFormSet(request.POST, instance=po)
        if form.is_valid() and formset.is_valid():
            form.save()
            lines = formset.save()
            messages.success(request, _("Purchase order updated."))
            return redirect("purchase_order_detail", po.id)
    else:
        form = PurchaseOrderForm(instance=po)
        formset = PurchaseOrderLineFormSet(instance=po)

    for f in formset.forms:
        if "item" in f.fields:
            f.fields["item"].queryset = Item.objects.filter(
                business=user_profile.business
            )

    context = {
        "form": form,
        "formset": formset,
        "po": po,
        "today": timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, "core/purchase_order_form.html", context)


# ── GOODS RECEIPTS ────────────────────────────────────────────────────────────


@login_required
@owner_required
def receive_goods(request, po_id):
    """
    Record a delivery against a PurchaseOrder.

    GET  — shows one form row per outstanding PO line, pre-filled with
           the PO unit price as the 'actual price' (edit if delivery price differs).
    POST — saves the GoodsReceipt header + lines, auto-creates Transaction records,
           optionally updates Item cost prices, then updates PO status.
    """
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect("home")

    po = get_object_or_404(PurchaseOrder, id=po_id, business=user_profile.business)

    if po.status in ("received", "cancelled"):
        messages.error(request, _("This PO is already fully received or cancelled."))
        return redirect("purchase_order_detail", po.id)

    outstanding_lines = [
        line
        for line in po.lines.select_related("item").all()
        if line.quantity_remaining() > 0
    ]

    if not outstanding_lines:
        messages.info(request, _("All items on this PO have already been received."))
        return redirect("purchase_order_detail", po.id)

    if request.method == "POST":
        receipt_form = GoodsReceiptForm(request.POST)
        line_formset = GoodsReceiptLineFormSet(request.POST)

        if receipt_form.is_valid() and line_formset.is_valid():
            receipt = receipt_form.save(commit=False)
            receipt.po = po
            receipt.received_by = request.user
            receipt.save()

            items_received = 0
            for form in line_formset.forms:
                data = form.cleaned_data
                qty = data.get("quantity_received", 0)
                if not qty:
                    continue

                po_line = get_object_or_404(
                    PurchaseOrderLine, id=data["po_line_id"], po=po
                )

                qty = min(qty, po_line.quantity_remaining())
                if qty <= 0:
                    continue

                GoodsReceiptLine.objects.create(
                    receipt=receipt,
                    po_line=po_line,
                    quantity_received=qty,
                    actual_unit_price=data["actual_unit_price"],
                    update_cost_price=data.get("update_cost_price", False),
                    notes=data.get("notes", ""),
                )

                po_line.quantity_received += qty
                po_line.save()

                if data.get("update_cost_price") and data.get("actual_unit_price"):
                    item = po_line.item
                    item.cost_price = data["actual_unit_price"]
                    item.save()

                invoice_ref = receipt.delivery_note_no or f"GR-{receipt.id}"
                Transaction.objects.create(
                    business=po.business,
                    item=po_line.item,
                    date=receipt.received_date,
                    invoice_no=invoice_ref,
                    type="Receipt",
                    qty=qty,
                    recipient=f"PO-{po.id}",
                )
                items_received += 1

            po.refresh_from_db()
            all_lines = list(po.lines.all())
            if all_lines and all(ln.quantity_remaining() == 0 for ln in all_lines):
                po.status = "received"
            else:
                po.status = "part_received"
            po.save()

            if items_received:
                messages.success(
                    request, _("Goods receipt recorded. Stock balances updated.")
                )
            else:
                messages.warning(
                    request, _("No quantities entered — nothing was saved.")
                )
            return redirect("purchase_order_detail", po.id)

    else:
        receipt_form = GoodsReceiptForm(
            initial={"received_date": timezone.now().date()}
        )
        initial_data = [
            {
                "po_line_id": line.id,
                "quantity_received": line.quantity_remaining(),
                "actual_unit_price": line.unit_price or 0,
                "update_cost_price": False,
            }
            for line in outstanding_lines
        ]
        line_formset = GoodsReceiptLineFormSet(initial=initial_data)

    line_form_pairs = list(zip(outstanding_lines, line_formset.forms))

    context = {
        "po": po,
        "receipt_form": receipt_form,
        "line_formset": line_formset,
        "line_form_pairs": line_form_pairs,
        "today": timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, "core/receive_goods.html", context)


@login_required
@owner_required
def goods_receipt_detail(request, receipt_id):
    """View a single goods receipt with line-by-line variance analysis."""
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect("home")

    receipt = get_object_or_404(
        GoodsReceipt.objects.select_related("po", "received_by").prefetch_related(
            "lines__po_line__item"
        ),
        id=receipt_id,
        po__business=user_profile.business,
    )
    context = {
        "receipt": receipt,
        "today": timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, "core/goods_receipt_detail.html", context)


# ── EXPORT STOCK ──────────────────────────────────────────────────────────────


@login_required
def export_stock_excel(request):
    user_profile = get_user_profile(request)
    store_id = request.GET.get("store")

    if user_profile:
        items = Item.objects.filter(store__business=user_profile.business)
        if store_id:
            items = items.filter(store_id=store_id)
    else:
        items = Item.objects.none()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Stock List"
    ws.append(
        [
            "Material No",
            "Description",
            "Unit",
            "Current Balance",
            "Reorder Level",
            "Selling Price",
            "Status",
            "Store",
        ]
    )

    for item in items:
        status = (
            "OUT OF STOCK"
            if item.current_balance() <= 0
            else "REORDER" if item.needs_reorder() else "AVAILABLE"
        )
        ws.append(
            [
                item.material_no,
                item.description,
                item.unit,
                item.current_balance(),
                item.reorder_level,
                float(item.selling_price) if item.selling_price else "",
                status,
                item.store.name,
            ]
        )

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = "attachment; filename=stock_list.xlsx"
    wb.save(response)
    return response


# ── MANAGE ITEMS ──────────────────────────────────────────────────────────────


@login_required
@owner_required
def manage_items(request):
    user_profile = request.user.userprofile
    items = (
        Item.objects.filter(business=user_profile.business)
        .select_related("store")
        .order_by("store__name", "description")
    )

    context = {
        "items": items,
        "today": timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, "core/manage_items.html", context)


@login_required
@owner_required
def add_item(request):
    user_profile = request.user.userprofile

    if request.method == "POST":
        form = ItemForm(
            request.POST, business=user_profile.business, show_cost_price=True
        )
        if form.is_valid():
            item = form.save(commit=False)
            item.business = user_profile.business
            if not item.material_no:
                last_item = (
                    Item.objects.filter(business=user_profile.business)
                    .order_by("id")
                    .last()
                )
                next_id = (last_item.id + 1) if last_item else 1
                item.material_no = f"MAT-{next_id:04d}"
            item.save()
            # Handle restriction fields (not in ItemForm — owner only)
            if user_profile.is_owner:
                item.is_restricted = request.POST.get('is_restricted') == 'on'
                item.restriction_notes = request.POST.get('restriction_notes', '').strip()
                try:
                    item.restricted_quantity = max(0, int(request.POST.get('restricted_quantity', 0)))
                except (ValueError, TypeError):
                    item.restricted_quantity = 0
                item.save(update_fields=['is_restricted', 'restriction_notes', 'restricted_quantity'])
            messages.success(
                request,
                _("'%(item_description)s' added successfully.")
                % {"item_description": item.description},
            )
            return redirect("manage_items")
    else:
        form = ItemForm(business=user_profile.business, show_cost_price=True)

    context = {
        "form": form,
        "today": timezone.now().strftime("%B %d, %Y"),
        "action": _("Add"),
        "is_add": True,
    }
    return render(request, "core/item_form.html", context)


@login_required
@owner_required
def edit_item(request, item_id):
    user_profile = request.user.userprofile
    item = get_object_or_404(Item, id=item_id, business=user_profile.business)

    if request.method == "POST":
        form = ItemForm(
            request.POST,
            instance=item,
            business=user_profile.business,
            show_cost_price=True,
        )
        if form.is_valid():
            item = form.save()
            # Handle restriction fields (not in ItemForm — owner only)
            if user_profile.is_owner:
                item.is_restricted = request.POST.get('is_restricted') == 'on'
                item.restriction_notes = request.POST.get('restriction_notes', '').strip()
                try:
                    item.restricted_quantity = max(0, int(request.POST.get('restricted_quantity', 0)))
                except (ValueError, TypeError):
                    item.restricted_quantity = 0
                item.save(update_fields=['is_restricted', 'restriction_notes', 'restricted_quantity'])
            messages.success(
                request,
                _("'%(item_description)s' updated successfully.")
                % {"item_description": item.description},
            )
            return redirect("manage_items")
    else:
        form = ItemForm(
            instance=item, business=user_profile.business, show_cost_price=True
        )

    context = {
        "form": form,
        "item": item,
        "today": timezone.now().strftime("%B %d, %Y"),
        "action": _("Edit"),
        "is_add": False,
    }
    return render(request, "core/item_form.html", context)


@login_required
@owner_required
def delete_item(request, item_id):
    user_profile = request.user.userprofile
    item = get_object_or_404(Item, id=item_id, business=user_profile.business)

    if request.method == "POST":
        item_name = item.description
        item.delete()
        messages.success(
            request,
            _("'%(item_name)s' deleted successfully.") % {"item_name": item_name},
        )
        return redirect("manage_items")

    context = {
        "item": item,
        "today": timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, "core/delete_item.html", context)


# ── MANAGE STORES ─────────────────────────────────────────────────────────────


@login_required
@owner_required
def manage_stores(request):
    user_profile = request.user.userprofile
    stores = Store.objects.filter(business=user_profile.business)

    if request.method == "POST":
        store_name = request.POST.get("name", "").strip()
        if store_name:
            Store.objects.create(name=store_name, business=user_profile.business)
            messages.success(
                request,
                _("Store '%(store_name)s' created successfully.")
                % {"store_name": store_name},
            )
            return redirect("manage_stores")
        else:
            messages.error(request, _("Store name cannot be empty."))

    context = {
        "stores": stores,
        "today": timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, "core/manage_stores.html", context)


# ── CUSTOMERS ─────────────────────────────────────────────────────────────────


@login_required
def customer_list(request):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect("home")

    from core.models import County as CoreCounty
    customers = Customer.objects.filter(business=user_profile.business).select_related('county')
    context = {
        "customers": customers,
        "counties": CoreCounty.objects.all().order_by('name'),
    }
    return render(request, "core/customer_list.html", context)


@login_required
def add_customer(request):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect("home")

    from core.models import County as CoreCounty

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        phone = request.POST.get("phone", "").strip()
        location = request.POST.get("location", "").strip()
        county_id = request.POST.get("county_id", "").strip()

        if name:
            county = None
            if county_id:
                try:
                    county = CoreCounty.objects.get(id=county_id)
                except CoreCounty.DoesNotExist:
                    pass
            Customer.objects.create(
                business=user_profile.business,
                name=name,
                phone=phone,
                location=location,
                county=county,
            )
            messages.success(
                request,
                _("Customer '%(customer_name)s' added.") % {"customer_name": name},
            )
            return redirect("customer_list")
        else:
            messages.error(request, _("Customer name is required."))

    return render(
        request,
        "core/customer_list.html",
        {
            "customers": Customer.objects.filter(business=user_profile.business).select_related('county'),
            "counties": CoreCounty.objects.all().order_by('name'),
        },
    )


@login_required
@owner_required
def delete_customer(request, customer_id):
    user_profile = request.user.userprofile
    customer = get_object_or_404(
        Customer, id=customer_id, business=user_profile.business
    )

    if request.method == "POST":
        customer.delete()
        messages.success(request, _("Customer deleted."))
    return redirect("customer_list")


# ── AJAX ──────────────────────────────────────────────────────────────────────


@login_required
def ajax_customers(request):
    """Returns customers for the transaction form dropdown."""
    user_profile = get_user_profile(request)
    if not user_profile:
        return JsonResponse([], safe=False)

    customers = Customer.objects.filter(business=user_profile.business).values(
        "id", "name", "phone"
    )
    return JsonResponse(list(customers), safe=False)


# ── SALES & P&L ───────────────────────────────────────────────────────────────


def get_date_range(period, date_from=None, date_to=None):
    """Returns (start_date, end_date) based on period filter."""
    today = date.today()
    if period == "today":
        return today, today
    elif period == "week":
        return today - timedelta(days=today.weekday()), today
    elif period == "month":
        return today.replace(day=1), today
    elif period == "year":
        return today.replace(month=1, day=1), today
    elif period == "custom" and date_from and date_to:
        try:
            from datetime import datetime

            return (
                datetime.strptime(date_from, "%Y-%m-%d").date(),
                datetime.strptime(date_to, "%Y-%m-%d").date(),
            )
        except ValueError:
            return today.replace(day=1), today
    else:
        return today.replace(day=1), today


@login_required
@owner_required
def sales_dashboard(request):
    user_profile = request.user.userprofile
    business = user_profile.business

    period = request.GET.get("period", "month")
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    start_date, end_date = get_date_range(period, date_from, date_to)

    sales = Transaction.objects.filter(
        business=business,
        type="Issue",
        date__gte=start_date,
        date__lte=end_date,
    ).select_related("item")

    total_revenue = sum(t.revenue() for t in sales)
    total_cost = sum(t.cost() for t in sales)
    total_profit = total_revenue - total_cost
    total_units_sold = sum(abs(t.qty) for t in sales)

    all_items = Item.objects.filter(business=business)
    stock_value = sum(item.stock_value() for item in all_items)

    from collections import defaultdict

    daily_revenue = defaultdict(float)
    daily_profit = defaultdict(float)
    for t in sales:
        day_str = str(t.date)
        daily_revenue[day_str] += t.revenue()
        daily_profit[day_str] += t.profit()

    sorted_dates = sorted(daily_revenue.keys())
    chart_labels = sorted_dates
    chart_revenue = [round(daily_revenue[d], 2) for d in sorted_dates]
    chart_profit = [round(daily_profit[d], 2) for d in sorted_dates]

    item_sales = defaultdict(
        lambda: {
            "description": "",
            "units_sold": 0,
            "revenue": 0.0,
            "cost": 0.0,
            "profit": 0.0,
        }
    )
    for t in sales:
        key = t.item.id
        item_sales[key]["description"] = t.item.description
        item_sales[key]["material_no"] = t.item.material_no
        item_sales[key]["units_sold"] += abs(t.qty)
        item_sales[key]["revenue"] += t.revenue()
        item_sales[key]["cost"] += t.cost()
        item_sales[key]["profit"] += t.profit()

    item_sales_list = sorted(
        item_sales.values(), key=lambda x: x["revenue"], reverse=True
    )
    top_items = sorted(item_sales_list, key=lambda x: x["units_sold"], reverse=True)[:5]

    sold_item_ids = set(t.item.id for t in sales)
    slow_items = all_items.exclude(id__in=sold_item_ids)[:10]

    profit_margin = (
        round((total_profit / total_revenue * 100), 1) if total_revenue > 0 else 0
    )

    context = {
        "period": period,
        "date_from": start_date,
        "date_to": end_date,
        "total_revenue": round(total_revenue, 2),
        "total_cost": round(total_cost, 2),
        "total_profit": round(total_profit, 2),
        "profit_margin": profit_margin,
        "total_units_sold": total_units_sold,
        "stock_value": round(stock_value, 2),
        "item_sales_list": item_sales_list,
        "top_items": top_items,
        "slow_items": slow_items,
        "chart_labels": json.dumps(chart_labels),
        "chart_revenue": json.dumps(chart_revenue),
        "chart_profit": json.dumps(chart_profit),
        "today": timezone.now().strftime("%B %d, %Y"),
    }
    return render(request, "core/sales_dashboard.html", context)


@login_required
@owner_required
def export_sales_excel(request):
    user_profile = request.user.userprofile
    business = user_profile.business

    period = request.GET.get("period", "month")
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    start_date, end_date = get_date_range(period, date_from, date_to)

    sales = (
        Transaction.objects.filter(
            business=business,
            type="Issue",
            date__gte=start_date,
            date__lte=end_date,
        )
        .select_related("item")
        .order_by("date")
    )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales Report"
    ws.append(
        [
            "Date",
            "Item",
            "Material No",
            "Units Sold",
            "Selling Price",
            "Cost Price",
            "Revenue",
            "Cost",
            "Profit",
            "Recipient",
            "Invoice No",
        ]
    )

    for t in sales:
        ws.append(
            [
                str(t.date),
                t.item.description,
                t.item.material_no,
                abs(t.qty),
                float(t.item.selling_price) if t.item.selling_price else "",
                float(t.item.cost_price) if t.item.cost_price else "",
                round(t.revenue(), 2),
                round(t.cost(), 2),
                round(t.profit(), 2),
                t.recipient or "—",
                t.invoice_no or "—",
            ]
        )

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = "attachment; filename=sales_report.xlsx"
    wb.save(response)
    return response


# ── NOTIFICATIONS ─────────────────────────────────────────────────────────────


@login_required
def notifications_list(request):
    notifications = request.user.app_notifications.all()[:50]
    request.user.app_notifications.filter(is_read=False).update(is_read=True)
    return render(request, "core/notifications.html", {"notifications": notifications})


@login_required
def notifications_count(request):
    count = request.user.app_notifications.filter(is_read=False).count()
    prompts_count = 0
    profile = getattr(request.user, "userprofile", None)
    if profile and profile.business:
        from .models import PendingTransactionPrompt

        prompts_count = PendingTransactionPrompt.objects.filter(
            business=profile.business, status="pending"
        ).count()
    return JsonResponse({"count": count, "prompts_count": prompts_count})


def daily_summary_webhook(request):
    """Endpoint called by cron-job.org to trigger daily summaries."""
    token = request.GET.get("token")
    expected = os.getenv("CRON_SECRET", "duka-mwecheche-cron-2026")
    if token != expected:
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden("Invalid token")

    from accounts.models import Business
    from .notifications import send_daily_summary

    logger = logging.getLogger(__name__)
    businesses = Business.objects.all().iterator(chunk_size=10)
    count = 0
    for business in businesses:
        count += 1
        try:
            send_daily_summary(business)
        except Exception:
            logger.exception("Daily summary failed for business %s", business.id)

    return JsonResponse({"status": "ok", "businesses": count})


# ── QUICK SELL ────────────────────────────────────────────────────────────────


@login_required
def quick_sell(request):
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect("home")

    success_data = None

    if request.method == "POST":
        cart_json = request.POST.get("cart", "[]")
        try:
            cart = json.loads(cart_json)
        except (json.JSONDecodeError, TypeError):
            cart = []

        recorded = []
        last_transaction = None

        for entry in cart:
            item = Item.objects.filter(
                id=entry.get("id"), store__business=user_profile.business
            ).first()
            if not item:
                continue

            qty = int(entry.get("qty", 0))
            if qty < 1:
                continue

            # ── RESTRICTED ITEM CHECK ─────────────────────────────────────
            can_override = getattr(user_profile, 'can_override_restrictions', False)
            if item.is_restricted and not user_profile.is_owner and not can_override:
                reserved = item.restricted_quantity or 0
                balance_after = item.current_balance() - qty
                if reserved == 0 or balance_after < reserved:
                    messages.warning(
                        request,
                        _(f'{item.description} requires owner approval for this quantity. '
                          f'Use Add Transaction to submit an approval request.')
                    )
                    continue
                # else: sale is within freely-sellable range — falls through
            # ─────────────────────────────────────────────────────────────

            if item.current_balance() < qty:
                messages.warning(
                    request,
                    _(
                        "Skipped %(item_description)s: only %(available)s %(unit)s in stock."
                    )
                    % {
                        "item_description": item.description,
                        "available": item.current_balance(),
                        "unit": item.unit,
                    },
                )
                continue

            last_transaction = Transaction.objects.create(
                item=item,
                type="Issue",
                qty=-qty,
                business=user_profile.business,
                payment_method=request.POST.get("payment_method", "cash"),
            )
            recorded.append(
                {
                    "name": item.description,
                    "qty": qty,
                    "subtotal": float(item.selling_price or 0) * qty,
                }
            )

        if recorded and last_transaction:
            total = sum(r["subtotal"] for r in recorded)

            try:
                from .notifications import notify_transaction_async

                daily_count = Transaction.objects.filter(
                    business=user_profile.business, date=date.today()
                ).count()
                notify_transaction_async(
                    last_transaction.id,
                    user_profile.business.id,
                    daily_count,
                    user_id=request.user.id,
                )
            except Exception:
                pass

            success_data = json.dumps({"items": recorded, "total": total})
            messages.success(
                request,
                _("Sale recorded: %(item_count)s item(s), KES %(total)s")
                % {
                    "item_count": len(recorded),
                    "total": f"{total:,.0f}",
                },
            )

    items_qs = list(
        Item.objects.filter(store__business=user_profile.business)
        .select_related("store")
        .order_by("description")
    )

    if items_qs:
        item_ids = [item.id for item in items_qs]
        txn_aggregates = (
            Transaction.objects.filter(item_id__in=item_ids)
            .values("item_id")
            .annotate(total_qty=Sum("qty"))
        )
        balance_lookup = {
            agg["item_id"]: (agg["total_qty"] or 0) for agg in txn_aggregates
        }
    else:
        balance_lookup = {}

    items = []
    for item in items_qs:
        txn_sum = balance_lookup.get(item.id, 0)
        balance = item.opening_bin_balance + txn_sum
        items.append(
            {
                "id": item.id,
                "description": item.description,
                "selling_price": item.selling_price,
                "balance": balance,
                "unit": item.unit,
                "store_id": item.store_id,
                "reorder_level": item.reorder_level,
            }
        )

    stores = Store.objects.filter(business=user_profile.business)

    return render(
        request,
        "core/quick_sell.html",
        {
            "items": items,
            "stores": stores,
            "success_data": success_data,
        },
    )


@login_required
@require_POST
def forecast_api(request):
    """
    POST /analytics/forecast/
    Body params (form or JSON):
        start_date  – YYYY-MM-DD
        end_date    – YYYY-MM-DD
        horizon     – int, days ahead (default 30)
        model       – 'ets' | 'regression' (default 'ets')
        item_id     – int or '' (optional)
    Returns JSON ready for Chart.js.
    """
    import json as _json

    try:
        if request.content_type == "application/json":
            body = _json.loads(request.body)
        else:
            body = request.POST

        start_str = body.get("start_date", "")
        end_str = body.get("end_date", "")
        horizon = int(body.get("horizon", 30))
        model = body.get("model", "ets").lower()
        item_id = body.get("item_id") or None

        if not start_str or not end_str:
            return JsonResponse(
                {"error": "start_date and end_date are required."}, status=400
            )

        start = date.fromisoformat(start_str)
        end = date.fromisoformat(end_str)

        if start >= end:
            return JsonResponse(
                {"error": "start_date must be before end_date."}, status=400
            )

        horizon = max(1, min(horizon, 365))

        profile = request.user.userprofile
        business = profile.business

        from core.models import Transaction

        qs = Transaction.objects.filter(
            business=business,
            type="Issue",
            date__gte=start,
            date__lte=end,
        )
        if item_id:
            qs = qs.filter(item_id=item_id)

        if model == "regression":
            result = run_regression(qs, start, end, horizon)
        else:
            result = run_ets(qs, start, end, horizon)

        return JsonResponse(result)

    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)
