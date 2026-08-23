"""
Owner consumption discipline — per-owner spend ceilings on "Mmiliki Alichukua".

2026-08-23 live request (Roy): "Owner consumption accountability: set an
amount and a window period limit setting to enhance owner discipline ... if
the limit is reached and a customer takes a drink in his name the system
rejects automatically ... and owner should receive report/notification via
mail of the same."

Design decisions, all deliberate:

  - PER OWNER, not per business. Roy: "mostly the owner is just one in these
    bars, regardless set it up for multiple owners just in case such a
    business comes up where there are more than one with different
    consumption limit settings for each." The limit therefore lives on
    UserProfile, and a draw records WHICH owner took it
    (Transaction.consumed_by) — recorded_by is whoever keyed the entry in,
    routinely a staff member, and cannot stand in for that.

  - SETTLED DRAWS DO NOT COUNT. The point is discipline about what the owner
    has taken and not yet paid back, not a hard cap on ever taking anything.
    Paying the business back frees the budget again, which is exactly the
    behaviour that makes the ceiling a discipline tool rather than a
    punishment. A settlement is an ordinary Transaction pointing back at the
    draw via settles_transaction (related_name='settlements').

  - VOIDED DRAWS DO NOT COUNT either — they were corrected away and never
    really happened (invoice_no='[OC-VOID]', the same tag
    owner_consumption_list already excludes).

  - No limit set (the default) means no gate at all, so nothing changes for
    any business that never configures one.
"""
import logging
from decimal import Decimal

from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)


def window_start(window, now=None):
    """Local-time start of the current window.

    Uses timezone.localtime() throughout rather than naive .date()/utcnow —
    this project is Africa/Nairobi (UTC+3) and a bar routinely trades across
    the UTC day boundary, so a UTC-derived "today" would silently attribute a
    late-night draw to the wrong window (a bug class this codebase has hit
    repeatedly; see the day-straddle entries in CLAUDE.md).
    """
    local = timezone.localtime(now) if now else timezone.localtime()
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if window == 'daily':
        return midnight
    if window == 'weekly':
        # Monday as week start, matching Kenyan business convention.
        return midnight - timedelta(days=local.weekday())
    return midnight.replace(day=1)  # monthly (the default)


def owner_consumption_usage(owner_profile, business, window=None, now=None):
    """KES of UNSETTLED owner draws attributed to this owner in the current
    window. Never raises — a limit check must not be able to block a real
    sale because of an unexpected query error."""
    from .models import Transaction
    window = window or owner_profile.consumption_limit_window or 'monthly'
    start = window_start(window, now=now)
    try:
        draws = (
            Transaction.objects
            .filter(
                business=business, type='OwnerConsumption',
                consumed_by=owner_profile, created_at__gte=start,
            )
            .exclude(invoice_no='[OC-VOID]')
            .prefetch_related('settlements')
        )
        total = Decimal('0')
        for t in draws:
            if t.settlements.exists():
                continue  # paid back — frees the budget again, by design
            total += (t.sale_amount or Decimal('0'))
        return total
    except Exception:
        logger.exception('Owner consumption usage failed for profile %s', owner_profile.id)
        return Decimal('0')


def resolve_draw_owner(business, owner_profile_id=None):
    """Which owner a draw should be attributed to.

    An explicit pick wins. Otherwise, when the business has exactly ONE
    owner-role profile, attribute to them automatically — that is the
    overwhelmingly common case (Roy: "mostly the owner is just one in these
    bars") and asking would be pure friction. With several owners and no
    pick, returns None: better an unattributed draw than one silently
    charged against the wrong person's ceiling.
    """
    owners = list(business.users.filter(role='owner').select_related('user'))
    if owner_profile_id:
        for o in owners:
            if str(o.id) == str(owner_profile_id):
                return o
        return None
    if len(owners) == 1:
        return owners[0]
    return None


def check_owner_consumption_limit(business, owner_profile, amount, now=None):
    """Would this draw breach the owner's own ceiling?

    Returns (allowed, info). `info` carries limit/used/remaining/window so the
    caller can explain the refusal precisely rather than just saying no.
    """
    if owner_profile is None:
        return True, None
    limit = owner_profile.consumption_limit_amount
    if not limit or limit <= 0:
        return True, None

    window = owner_profile.consumption_limit_window or 'monthly'
    used = owner_consumption_usage(owner_profile, business, window=window, now=now)
    amount = Decimal(str(amount or 0))
    info = {
        'limit': limit,
        'used': used,
        'remaining': max(Decimal('0'), limit - used),
        'window': window,
        'window_label': dict(owner_profile.CONSUMPTION_WINDOW_CHOICES).get(window, window),
        'attempted': amount,
        'owner_name': owner_profile.user.get_full_name() or owner_profile.user.username,
    }
    return (used + amount) <= limit, info


def notify_owner_limit_reached(business, owner_profile, info, item_name=''):
    """Email + in-app notice to the owner whose ceiling just refused a draw.

    Email specifically, per Roy's own wording ("owner should receive report/
    notification via mail of the same"). Fire-and-forget: a notification
    failing must never change whether the draw was blocked.
    """
    from core.notifications import create_in_app_notification, send_email_notification_async
    try:
        who = info['owner_name']
        subject = f"{business.name}: kikomo cha matumizi kimefikiwa"
        body = (
            f"<p>Habari {who},</p>"
            f"<p>Ombi la kuchukua <strong>{item_name or 'bidhaa'}</strong> "
            f"(KES {info['attempted']:,.0f}) limekataliwa kwa sababu kikomo chako cha "
            f"matumizi kimefikiwa.</p>"
            f"<ul>"
            f"<li>Kikomo ({info['window_label']}): KES {info['limit']:,.0f}</li>"
            f"<li>Umeshatumia: KES {info['used']:,.0f}</li>"
            f"<li>Iliyobaki: KES {info['remaining']:,.0f}</li>"
            f"</ul>"
            f"<p>Ukilipa deni la ulichochukua, kikomo kitafunguka tena.</p>"
        )
        email = getattr(owner_profile.user, 'email', '') or ''
        if email:
            send_email_notification_async(email, subject, body)
        create_in_app_notification(
            owner_profile.user,
            title='🚫 Kikomo cha matumizi kimefikiwa',
            message=(
                f"Ombi la {item_name or 'bidhaa'} (KES {info['attempted']:,.0f}) limekataliwa. "
                f"Kikomo {info['window_label']}: KES {info['limit']:,.0f}, "
                f"umeshatumia KES {info['used']:,.0f}."
            ),
            notification_type='warning',
            link_url='/stock/owner-consumption/list/',
        )
    except Exception:
        logger.exception('Owner limit notification failed for profile %s', owner_profile.id)


def notify_owner_of_transfer_to_their_name(business, owner_profile, source_customer, amount):
    """Email + in-app when a bill is proposed onto the owner's own name.

    Roy, same request: "the same mail notifications should be applicable to
    tab transfers to the owner's name." The owner should never find out after
    the fact that something landed against him.
    """
    from core.notifications import create_in_app_notification, send_email_notification_async
    try:
        who = owner_profile.user.get_full_name() or owner_profile.user.username
        subject = f"{business.name}: bili imehamishiwa kwa jina lako"
        body = (
            f"<p>Habari {who},</p>"
            f"<p><strong>{source_customer}</strong> ameomba kuhamishia bili ya "
            f"<strong>KES {Decimal(str(amount)):,.0f}</strong> kwenye jina lako "
            f"(Mmiliki Alichukua).</p>"
            f"<p>Ombi hili linasubiri uamuzi wako kwenye mfumo.</p>"
        )
        email = getattr(owner_profile.user, 'email', '') or ''
        if email:
            send_email_notification_async(email, subject, body)
        create_in_app_notification(
            owner_profile.user,
            title='🔀 Bili imehamishiwa kwa jina lako',
            message=f'{source_customer} — KES {Decimal(str(amount)):,.0f}. Inasubiri uamuzi wako.',
            notification_type='info',
            link_url='/stock/owner-consumption/list/',
        )
    except Exception:
        logger.exception('Owner transfer notification failed for profile %s', owner_profile.id)
