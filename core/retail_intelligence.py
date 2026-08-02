"""
UBA §7.3 (Sprint R2) — the margin guard and sale-below-cost check. Both are
BusinessException producers (M3), additive alongside existing Notification/
SMS delivery, same pattern already established for close_shift()'s and
StockTransfer's producers.
"""
from decimal import Decimal

from .models import BusinessException, Notification


def check_margin_guard(item, old_cost, new_cost, business):
    """Called from add_transaction()'s Receipt cost-price-update block —
    the ONE place Item.cost_price is ever written — right after old_cost
    already confirmed to exceed margin_alert_pct. Computes a suggested new
    selling price that preserves the OLD margin ratio (old_price/old_cost),
    and fires one BusinessException + owner/manager Notification+SMS with
    that suggestion (R2-AC1: "exactly one exception + one SMS")."""
    old_price = item.selling_price
    suggested_price = None
    if old_price and old_cost:
        suggested_price = (Decimal(str(new_cost)) * (Decimal(str(old_price)) / Decimal(str(old_cost)))).quantize(Decimal('0.01'))

    pct_rise = ((Decimal(str(new_cost)) - Decimal(str(old_cost))) / Decimal(str(old_cost)) * 100) if old_cost else Decimal('0')

    detail = (
        f"Bei ya {item.description} imepanda kutoka KES {old_cost} hadi KES {new_cost} "
        f"({pct_rise:.0f}%)."
    )
    if suggested_price:
        detail += f" Pendekezo: uza kwa KES {suggested_price} kudumisha faida yako."

    exc = BusinessException.raise_exception(
        business=business, kind='cost_rise', severity='warn',
        title=f"Gharama ya {item.description} imepanda",
        detail=detail, amount_kes=(Decimal(str(new_cost)) - Decimal(str(old_cost))),
        link_url=f'/item/{item.id}/',
    )

    from accounts.models import UserProfile
    from .notifications import normalize_ke_phone, send_sms_notification
    for op in UserProfile.objects.filter(business=business, role__in=['owner', 'manager']).select_related('user'):
        try:
            Notification.objects.create(
                user=op.user, title='📈 Bei Imepanda', message=detail,
                notification_type='warning', link_url=f'/item/{item.id}/',
            )
        except Exception:
            pass
        if op.phone:
            try:
                send_sms_notification(detail, normalize_ke_phone(op.phone))
            except Exception:
                pass
    return exc, suggested_price


def check_sale_below_cost(item, sale_amount, qty, business, is_owner):
    """Called at checkout for a plain (non-produce/non-keg/non-kitchen-batch)
    item line. Returns (blocked: bool, message: str or None). Staff: blocked
    outright. Owner: allowed through with a warning, still logged — R2-AC2
    ("Selling below cost is blocked for staff and warned for owner, and
    logged")."""
    if not item.cost_price or qty <= 0:
        return False, None
    per_unit = Decimal(str(sale_amount)) / Decimal(str(qty))
    if per_unit >= item.cost_price:
        return False, None

    loss = (Decimal(str(item.cost_price)) - per_unit) * Decimal(str(qty))
    BusinessException.raise_exception(
        business=business, kind='below_cost', severity='danger' if not is_owner else 'warn',
        title=f"Uuzaji chini ya gharama — {item.description}",
        detail=f"Iliuzwa kwa KES {per_unit:.2f}/kila moja, gharama ni KES {item.cost_price}.",
        amount_kes=loss, link_url=f'/item/{item.id}/',
    )
    if is_owner:
        return False, f"Onyo: {item.description} imeuzwa chini ya gharama (KES {per_unit:.2f} < KES {item.cost_price})."
    return True, f"Bidhaa {item.description} haiwezi kuuzwa chini ya bei ya gharama (KES {item.cost_price})."
