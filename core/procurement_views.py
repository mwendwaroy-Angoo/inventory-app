"""
Procurement system views.

Owners:
    - Create procurement requests
    - Evaluate bids (auto-scored)
    - Review supplier applications
    - Award bids → auto-create SupplierRelationship

Suppliers (other businesses):
    - Browse open procurement requests
    - Submit bids
    - Apply to become a supplier to a business
    - View their own bids
"""

import math
from decimal import Decimal

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages as django_messages
from django.utils import timezone
from django.db.models import Avg, Q

from accounts.models import Business
from .models import (
    ProcurementRequest,
    SupplierBid,
    SupplierApplication,
    SupplierRelationship,
    Feedback,
    BusinessType,
    PurchaseOrder,
    PurchaseOrderLine,
    SupplierBidLine,
)

# ── SCORING ENGINE ───────────────────────────────────────────────────────────


def _haversine_km(lat1, lon1, lat2, lon2):
    """Distance in km between two lat/lng points."""
    if not all([lat1, lon1, lat2, lon2]):
        return None
    R = 6371
    la1, lo1 = math.radians(float(lat1)), math.radians(float(lon1))
    la2, lo2 = math.radians(float(lat2)), math.radians(float(lon2))
    dlat, dlon = la2 - la1, lo2 - lo1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(la1) * math.cos(la2) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def score_bid(bid, procurement):
    """
    Calculate a composite score (0–100) for a supplier bid.

    Weights:
        Price competitiveness   40%
        Proximity               25%
        Supplier rating         20%
        Service relevance       10%
        Response speed           5%
    """
    score = Decimal("0")
    buyer = procurement.business
    supplier = bid.supplier

    # ── 1. Price (40 pts) — lower is better ─────────────────────────────────
    budget = procurement.budget_max or procurement.budget_min
    if budget and budget > 0:
        ratio = float(bid.amount) / float(budget)
        if ratio <= 0.5:
            price_score = 40
        elif ratio <= 1.0:
            price_score = 40 * (1 - (ratio - 0.5) / 0.5)
        elif ratio <= 1.5:
            price_score = max(0, 40 * (1 - (ratio - 1.0)))
        else:
            price_score = 0
    else:
        # No budget reference — all bids get base score, ranked relatively later
        price_score = 20
    score += Decimal(str(round(price_score, 2)))

    # ── 2. Proximity (25 pts) — county match + coordinate distance ──────────
    proximity_score = 0
    # County-level match (10 pts of 25)
    if supplier.county and buyer.county:
        if supplier.county_id == buyer.county_id:
            proximity_score += 10
        elif supplier.county and buyer.county:
            # Different county — partial credit if nearby sub-county (same region)
            proximity_score += 3

    # Coordinate distance (15 pts of 25)
    dist = _haversine_km(
        buyer.latitude,
        buyer.longitude,
        supplier.latitude,
        supplier.longitude,
    )
    if dist is not None:
        if dist <= 5:
            proximity_score += 15
        elif dist <= 20:
            proximity_score += 15 * (1 - (dist - 5) / 15)
        elif dist <= 100:
            proximity_score += max(0, 15 * (1 - (dist - 20) / 80) * 0.5)
        # >100 km gets 0
    else:
        # No coordinates — award partial based on county match only
        if supplier.county_id == (buyer.county_id if buyer.county else None):
            proximity_score += 5
    score += Decimal(str(round(proximity_score, 2)))

    # ── 3. Supplier Rating (20 pts) — average feedback from other businesses ─
    avg_rating = Feedback.objects.filter(
        to_business=supplier,
        feedback_type="business_to_supplier",
    ).aggregate(avg=Avg("rating"))["avg"]

    if avg_rating:
        rating_score = (avg_rating / 5.0) * 20
    else:
        rating_score = 10  # No reviews yet — neutral
    score += Decimal(str(round(rating_score, 2)))

    # ── 4. Service Relevance (10 pts) — business type match ─────────────────
    relevance_score = 0
    if procurement.category and supplier.business_type:
        if supplier.business_type_id == procurement.category_id:
            relevance_score = 10
        else:
            relevance_score = 2  # Different type but still on platform
    else:
        relevance_score = 5  # No category specified
    score += Decimal(str(round(relevance_score, 2)))

    # ── 5. Response Speed (5 pts) — how quickly the bid was placed ──────────
    hours_elapsed = (bid.created_at - procurement.created_at).total_seconds() / 3600
    total_hours = (
        ((procurement.deadline - procurement.created_at.date()).days * 24)
        if procurement.deadline > procurement.created_at.date()
        else 24
    )

    if total_hours > 0:
        speed_ratio = hours_elapsed / total_hours
        if speed_ratio <= 0.25:
            speed_score = 5
        elif speed_ratio <= 0.5:
            speed_score = 3
        elif speed_ratio <= 0.75:
            speed_score = 1
        else:
            speed_score = 0
    else:
        speed_score = 2.5
    score += Decimal(str(round(speed_score, 2)))

    return min(score, Decimal("100"))


def score_all_bids(procurement):
    """Re-score all bids for a procurement request and save."""
    for bid in procurement.bids.all():
        bid.score = score_bid(bid, procurement)
        bid.save(update_fields=["score"])


# ── OWNER: PROCUREMENT REQUESTS ──────────────────────────────────────────────


def _close_expired_procurement():
    """Auto-close any open procurement requests past their deadline."""
    from django.utils import timezone

    expired = ProcurementRequest.objects.filter(
        status="open",
        deadline__lt=timezone.now().date(),
    )
    count = expired.update(status="closed")
    return count


@login_required
def procurement_list_owner(request):
    """Owner view: all their procurement requests."""
    profile = getattr(request.user, "userprofile", None)
    if not profile or not profile.is_owner or not profile.business:
        return redirect("home")

    # Auto-close expired ones
    _close_expired_procurement()

    requests = ProcurementRequest.objects.filter(business=profile.business)

    status_filter = request.GET.get("status", "")
    if status_filter:
        requests = requests.filter(status=status_filter)

    return render(
        request,
        "procurement/my_requests.html",
        {
            "requests": requests,
            "status_filter": status_filter,
        },
    )


@login_required
def create_procurement(request):
    """Owner: create a new procurement request."""
    profile = getattr(request.user, "userprofile", None)
    if not profile or not profile.is_owner or not profile.business:
        return redirect("home")

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        description = request.POST.get("description", "").strip()
        category_id = request.POST.get("category", "")
        budget_min = request.POST.get("budget_min", "") or None
        budget_max = request.POST.get("budget_max", "") or None
        deadline = request.POST.get("deadline", "")

        if not title or not description or not deadline:
            django_messages.error(
                request, "Title, description, and deadline are required."
            )
            return redirect("create_procurement")

        pr = ProcurementRequest.objects.create(
            business=profile.business,
            title=title,
            description=description,
            category_id=category_id if category_id else None,
            budget_min=budget_min,
            budget_max=budget_max,
            deadline=deadline,
        )

        # Notify all approved suppliers about the new bid opportunity
        try:
            from .notifications import notify_new_bid_opportunity

            notify_new_bid_opportunity(pr)
        except Exception:
            pass  # Don't block procurement creation if notifications fail

        django_messages.success(request, f'Procurement "{pr.title}" created.')
        return redirect("procurement_list_owner")

    categories = BusinessType.objects.all()
    return render(
        request,
        "procurement/create_procurement.html",
        {
            "categories": categories,
        },
    )


@login_required
def procurement_detail(request, pk):
    """View a procurement request + bids (owner or supplier perspective)."""
    procurement = get_object_or_404(ProcurementRequest, pk=pk)
    profile = getattr(request.user, "userprofile", None)

    # Auto-close if expired and still open
    if procurement.status == "open" and procurement.deadline < timezone.now().date():
        procurement.status = "closed"
        procurement.save(update_fields=["status"])

    is_owner = (
        profile
        and profile.business
        and profile.business == procurement.business
        and profile.is_owner
    )

    bids = procurement.bids.select_related(
        "supplier__county", "supplier__business_type"
    )
    if is_owner:
        bids = bids.order_by("-score", "amount")
    else:
        bids = (
            bids.filter(supplier=profile.business)
            if profile and profile.business
            else bids.none()
        )

    # Check if current user's business already bid
    already_bid = False
    if profile and profile.business:
        already_bid = procurement.bids.filter(supplier=profile.business).exists()

    return render(
        request,
        "procurement/procurement_detail.html",
        {
            "procurement": procurement,
            "bids": bids,
            "is_owner": is_owner,
            "already_bid": already_bid,
        },
    )


@login_required
@require_POST
def cancel_procurement(request, pk):
    """'cancelled' was a valid STATUS_CHOICES value with no view that ever
    set it — a buyer had no way to withdraw a posted request. Allowed from
    open/evaluating only; a bid already having been awarded means suppliers
    have committed, so cancelling from 'awarded' isn't offered here (the
    buyer would need to un-award first, which is a separate, not-yet-built
    concern — out of scope for closing this specific gap)."""
    profile = getattr(request.user, "userprofile", None)
    if not profile or not profile.is_owner or not profile.business:
        return redirect("home")

    procurement = get_object_or_404(
        ProcurementRequest, pk=pk, business=profile.business
    )

    if procurement.status in ("open", "evaluating"):
        procurement.status = "cancelled"
        procurement.save(update_fields=["status"])
        django_messages.success(request, "Procurement request cancelled.")
    elif procurement.status == "cancelled":
        django_messages.info(request, "This request is already cancelled.")
    else:
        django_messages.error(
            request, "This request can no longer be cancelled from its current state."
        )
    return redirect("procurement_detail", pk=procurement.pk)


@login_required
def evaluate_bids(request, pk):
    """Owner: trigger scoring and view ranked bids."""
    profile = getattr(request.user, "userprofile", None)
    if not profile or not profile.is_owner or not profile.business:
        return redirect("home")

    procurement = get_object_or_404(
        ProcurementRequest, pk=pk, business=profile.business
    )

    # Re-score all bids
    score_all_bids(procurement)

    if procurement.status == "open":
        procurement.status = "evaluating"
        procurement.save(update_fields=["status"])

    bids = procurement.bids.select_related(
        "supplier__county", "supplier__business_type"
    ).order_by("-score", "amount")

    # Top 3 shortlisted
    for i, bid in enumerate(bids):
        if i < 3 and bid.status == "submitted":
            bid.status = "shortlisted"
            bid.save(update_fields=["status"])

    return render(
        request,
        "procurement/evaluate_bids.html",
        {
            "procurement": procurement,
            "bids": bids,
        },
    )


@login_required
def award_bid(request, bid_id):
    """Owner: accept a bid → creates SupplierRelationship."""
    profile = getattr(request.user, "userprofile", None)
    if not profile or not profile.is_owner or not profile.business:
        return redirect("home")

    bid = get_object_or_404(
        SupplierBid, pk=bid_id, procurement__business=profile.business
    )

    if request.method == "POST":
        # Lock + re-check before writing — a double-click or slow-network
        # retry on this button used to be able to run this whole block
        # twice, creating a second draft PurchaseOrder (with duplicated
        # lines) and re-firing supplier award notifications/emails, since
        # nothing here checked whether the bid was already accepted.
        from django.db import transaction as db_transaction
        with db_transaction.atomic():
            bid = SupplierBid.objects.select_for_update().get(pk=bid.pk)
            if bid.status == "accepted":
                django_messages.info(
                    request, "This bid has already been awarded."
                )
                return redirect("procurement_detail", pk=bid.procurement.pk)

            bid.status = "accepted"
            bid.save(update_fields=["status"])

            # Reject other bids
            bid.procurement.bids.exclude(pk=bid.pk).update(status="rejected")

            # Mark procurement as awarded
            bid.procurement.status = "awarded"
            bid.procurement.save(update_fields=["status"])

        # Notify the supplier that their bid was awarded
        try:
            from .notifications import notify_supplier_bid_awarded

            notify_supplier_bid_awarded(bid)
        except Exception:
            pass  # Don't block bid award if notifications fail

        # Auto-create supplier relationship
        SupplierRelationship.objects.get_or_create(
            business=profile.business,
            supplier=bid.supplier,
            defaults={"notes": f"Awarded via procurement: {bid.procurement.title}"},
        )

        # Auto-create a draft Purchase Order for the awarded supplier so owner can confirm lines
        try:
            # Estimate expected delivery date from delivery_timeline (simple parse)
            from datetime import timedelta
            import re

            expected = None
            dt = bid.delivery_timeline or ""
            m = re.search(r"(\d+)", dt)
            if m:
                n = int(m.group(1))
                if "week" in dt.lower():
                    days = n * 7
                else:
                    days = n
                expected = timezone.now().date() + timedelta(days=days)

            po = PurchaseOrder.objects.create(
                business=profile.business,
                supplier=bid.supplier,
                awarded_bid=bid,
                status="draft",
                expected_delivery_date=expected,
                created_by=request.user,
                notes=f"Auto-created from procurement award: {bid.procurement.title} — Bid {bid.id} by {bid.supplier.name}",
            )
            # If the bid included itemised lines, create PO lines from them
            try:
                for bl in getattr(bid, "lines", []).all():
                    if bl.item and bl.quantity and bl.quantity > 0:
                        PurchaseOrderLine.objects.create(
                            po=po,
                            item=bl.item,
                            quantity_ordered=bl.quantity,
                            unit_price=bl.unit_price or bl.item.cost_price or 0,
                        )
            except Exception:
                # ignore failures creating PO lines — owner can edit PO manually
                pass
        except Exception:
            po = None
        # Notify owner and supplier about the created draft PO (if any)
        try:
            from core.notifications import (
                create_in_app_notification,
                send_email_notification,
            )
            from django.template.loader import render_to_string

            if po:
                # Notify the awarding owner (request.user)
                create_in_app_notification(
                    request.user,
                    f"Draft PO-{po.id} created",
                    f"A draft purchase order for {bid.supplier.name} was created from procurement award.",
                    notification_type="order",
                )

                # Notify supplier owner (if present) via in-app and email
                supplier_owner_profile = bid.supplier.users.filter(role="owner").first()
                if supplier_owner_profile:
                    create_in_app_notification(
                        supplier_owner_profile.user,
                        f"Procurement Award — {profile.business.name}",
                        f"You were awarded procurement '{bid.procurement.title}'. A draft PO ({po.id}) has been created.",
                        notification_type="order",
                    )

                    # Render and send supplier award email (plain + HTML)
                    try:
                        subject = f"Procurement awarded: {bid.procurement.title}"
                        text_message = render_to_string(
                            "emails/supplier_awarded.txt",
                            {
                                "bid": bid,
                                "po": po,
                                "awarding_business": profile.business,
                                "supplier_owner": supplier_owner_profile,
                            },
                        )
                        html_message = render_to_string(
                            "emails/supplier_awarded.html",
                            {
                                "bid": bid,
                                "po": po,
                                "awarding_business": profile.business,
                                "supplier_owner": supplier_owner_profile,
                                "request": request,
                            },
                        )
                        recipient_email = (
                            supplier_owner_profile.user.email or bid.supplier.email
                        )
                        if recipient_email:
                            from core.notifications import send_email_notification

                            send_email_notification(
                                recipient_email,
                                subject,
                                html_message,
                                text_message=text_message,
                            )
                    except Exception:
                        # don't block main flow if email fails
                        pass
        except Exception:
            pass

        django_messages.success(
            request,
            f"Bid from {bid.supplier.name} accepted! They are now in your supplier list.",
        )
        return redirect("procurement_detail", pk=bid.procurement.pk)

    return render(request, "procurement/award_bid.html", {"bid": bid})


# ── BID COMPLETION WORKFLOW ──────────────────────────────────────────────────


@login_required
def confirm_delivery(request, bid_id):
    """Owner: confirm that delivery from the supplier was successful.

    Once delivery is confirmed AND the supplier confirms payment,
    the bid is marked fully completed and pending bids are auto-cleared.
    """
    profile = getattr(request.user, "userprofile", None)
    if not profile or not profile.is_owner or not profile.business:
        return redirect("home")

    bid = get_object_or_404(
        SupplierBid, pk=bid_id, procurement__business=profile.business
    )
    if bid.status != "accepted":
        django_messages.error(request, "Only awarded bids can be completed.")
        return redirect("procurement_detail", pk=bid.procurement.pk)

    if bid.is_delivery_confirmed():
        django_messages.info(request, "Delivery already confirmed.")
        return redirect("procurement_detail", pk=bid.procurement.pk)

    if request.method == "POST":
        bid.delivery_confirmed_at = timezone.now()
        bid.save(update_fields=["delivery_confirmed_at"])

        # Confirming delivery here and actually receiving the linked PO
        # (receive_goods(), which is what updates stock) are two completely
        # separate state machines connected only by the awarded_bid FK — an
        # owner could confirm delivery and let the procurement close as
        # "done" while the draft PO sits unreceived forever and zero stock
        # was ever added. Not auto-linking them (that would silently do a
        # stock movement the owner never explicitly reviewed) — just making
        # the gap visible instead of leaving it invisible.
        unreceived_po = bid.purchase_orders.exclude(status__in=("received", "cancelled")).first()
        if unreceived_po:
            django_messages.warning(
                request,
                f"Delivery confirmed, but PO-{unreceived_po.id} (auto-created from this award) "
                f"hasn't been marked received yet — stock won't reflect this delivery until you "
                f"record the receipt.",
            )

        # Notify the supplier owner that delivery was confirmed
        try:
            from core.notifications import create_in_app_notification

            supplier_owner_profile = bid.supplier.users.filter(role="owner").first()
            if supplier_owner_profile:
                create_in_app_notification(
                    supplier_owner_profile.user,
                    f"Delivery Confirmed — {profile.business.name}",
                    f"The business owner confirmed delivery for procurement '{bid.procurement.title}'.",
                    notification_type="order",
                )
        except Exception:
            pass

        # If both confirmed, close out the bid
        if bid.is_fully_completed():
            bid.procurement.status = "closed"
            bid.procurement.save(update_fields=["status"])
            django_messages.success(
                request,
                "Delivery confirmed! Payment was already confirmed. Procurement request closed.",
            )
        else:
            django_messages.success(
                request,
                "Delivery confirmed! Awaiting supplier payment confirmation to close the request.",
            )
        return redirect("procurement_detail", pk=bid.procurement.pk)

    return render(request, "procurement/confirm_delivery.html", {"bid": bid})


@login_required
def confirm_payment(request, bid_id):
    """Supplier: confirm that payment from the business owner was received.

    Once payment is confirmed AND the owner confirms delivery,
    the bid is marked fully completed and pending bids are auto-cleared.
    """
    profile = getattr(request.user, "userprofile", None)
    if not profile or not profile.business:
        return redirect("home")

    bid = get_object_or_404(SupplierBid, pk=bid_id, supplier=profile.business)
    if bid.status != "accepted":
        django_messages.error(request, "Only awarded bids can be completed.")
        return redirect("procurement_detail", pk=bid.procurement.pk)

    if bid.is_payment_confirmed():
        django_messages.info(request, "Payment already confirmed.")
        return redirect("procurement_detail", pk=bid.procurement.pk)

    if request.method == "POST":
        bid.payment_confirmed_at = timezone.now()
        bid.save(update_fields=["payment_confirmed_at"])

        # Notify the owner that payment was confirmed
        try:
            from core.notifications import create_in_app_notification

            owner_profile = bid.procurement.business.users.filter(role="owner").first()
            if owner_profile:
                create_in_app_notification(
                    owner_profile.user,
                    f"Payment Confirmed — {profile.business.name}",
                    f"The supplier confirmed payment receipt for procurement '{bid.procurement.title}'.",
                    notification_type="order",
                )
        except Exception:
            pass

        # If both confirmed, close out the bid
        if bid.is_fully_completed():
            bid.procurement.status = "closed"
            bid.procurement.save(update_fields=["status"])
            django_messages.success(
                request,
                "Payment confirmed! Delivery was already confirmed. Procurement request closed.",
            )
        else:
            django_messages.success(
                request,
                "Payment confirmed! Awaiting owner delivery confirmation to close the request.",
            )
        return redirect("procurement_detail", pk=bid.procurement.pk)

    return render(request, "procurement/confirm_payment.html", {"bid": bid})


# ── SUPPLIER: BROWSE & BID ──────────────────────────────────────────────────


@login_required
def procurement_browse(request):
    """Supplier: browse open procurement requests from other businesses."""
    profile = getattr(request.user, "userprofile", None)
    if not profile or not profile.business:
        return redirect("home")

    procurements = (
        ProcurementRequest.objects.filter(
            status="open",
            deadline__gte=timezone.now().date(),
        )
        .exclude(business=profile.business)
        .select_related(
            "business__county",
            "category",
        )
    )

    query = request.GET.get("q", "").strip()
    category_id = request.GET.get("category", "")
    if query:
        procurements = procurements.filter(
            Q(title__icontains=query) | Q(description__icontains=query)
        )
    if category_id:
        procurements = procurements.filter(category_id=category_id)

    categories = BusinessType.objects.all()

    return render(
        request,
        "procurement/browse.html",
        {
            "procurements": procurements,
            "categories": categories,
            "query": query,
            "selected_category": category_id,
        },
    )


@login_required
def submit_bid(request, pk):
    """Supplier: submit a bid on a procurement request."""
    profile = getattr(request.user, "userprofile", None)
    if not profile or not profile.business:
        return redirect("home")

    procurement = get_object_or_404(ProcurementRequest, pk=pk)

    if not procurement.is_accepting_bids:
        django_messages.error(request, "This procurement is no longer accepting bids.")
        return redirect("procurement_browse")

    if procurement.business == profile.business:
        django_messages.error(
            request, "You cannot bid on your own procurement request."
        )
        return redirect("procurement_browse")

    if SupplierBid.objects.filter(
        procurement=procurement, supplier=profile.business
    ).exists():
        django_messages.warning(request, "You have already submitted a bid.")
        return redirect("procurement_detail", pk=pk)

    if request.method == "POST":
        amount = request.POST.get("amount", "").strip()
        delivery_timeline = request.POST.get("delivery_timeline", "").strip()
        proposal = request.POST.get("proposal", "").strip()

        if not amount or not delivery_timeline or not proposal:
            django_messages.error(request, "All fields are required.")
            return redirect("submit_bid", pk=pk)

        try:
            amount_val = Decimal(amount)
        except Exception:
            django_messages.error(request, "Invalid amount.")
            return redirect("submit_bid", pk=pk)

        bid = SupplierBid.objects.create(
            procurement=procurement,
            supplier=profile.business,
            amount=amount_val,
            delivery_timeline=delivery_timeline,
            proposal=proposal,
        )
        # Score immediately
        bid.score = score_bid(bid, procurement)
        bid.save(update_fields=["score"])

        # Notify the requesting business owner about the new bid
        try:
            from .notifications import notify_supplier_bid_received

            notify_supplier_bid_received(bid)
        except Exception:
            pass  # Don't block bid submission if notifications fail

        django_messages.success(request, "Your bid has been submitted.")
        return redirect("procurement_detail", pk=pk)

    return render(
        request,
        "procurement/submit_bid.html",
        {
            "procurement": procurement,
        },
    )


@login_required
def my_bids(request):
    """Supplier: view all bids they've submitted."""
    profile = getattr(request.user, "userprofile", None)
    if not profile or not profile.business:
        return redirect("home")

    bids = SupplierBid.objects.filter(
        supplier=profile.business,
    ).select_related("procurement__business", "procurement__category")

    status_filter = request.GET.get("status", "")
    if status_filter:
        bids = bids.filter(status=status_filter)

    return render(
        request,
        "procurement/my_bids.html",
        {
            "bids": bids,
            "status_filter": status_filter,
        },
    )


# ── SUPPLIER APPLICATIONS ───────────────────────────────────────────────────


@login_required
def apply_as_supplier(request, business_id):
    """A business applies to become a supplier to another business."""
    profile = getattr(request.user, "userprofile", None)
    if not profile or not profile.business:
        return redirect("home")

    target = get_object_or_404(Business, pk=business_id)

    if target == profile.business:
        django_messages.error(request, "You cannot apply to your own business.")
        return redirect("shop_home")

    if SupplierApplication.objects.filter(
        applicant=profile.business,
        target_business=target,
    ).exists():
        django_messages.warning(request, "You have already applied to this business.")
        return redirect("shop_home")

    if request.method == "POST":
        services = request.POST.get("services_offered", "").strip()
        cover = request.POST.get("cover_letter", "").strip()
        if not services or not cover:
            django_messages.error(request, "All fields are required.")
            return redirect("apply_as_supplier", business_id=business_id)

        SupplierApplication.objects.create(
            applicant=profile.business,
            target_business=target,
            services_offered=services,
            cover_letter=cover,
        )
        django_messages.success(request, f"Application sent to {target.name}.")
        return redirect("shop_home")

    return render(
        request,
        "procurement/apply_as_supplier.html",
        {
            "target": target,
        },
    )


@login_required
def supplier_applications(request):
    """Owner: view incoming supplier applications."""
    profile = getattr(request.user, "userprofile", None)
    if not profile or not profile.is_owner or not profile.business:
        return redirect("home")

    apps = SupplierApplication.objects.filter(
        target_business=profile.business,
    ).select_related("applicant__business_type", "applicant__county")

    status_filter = request.GET.get("status", "")
    if status_filter:
        apps = apps.filter(status=status_filter)

    return render(
        request,
        "procurement/supplier_applications.html",
        {
            "applications": apps,
            "status_filter": status_filter,
        },
    )


@login_required
def review_application(request, app_id):
    """Owner: approve or reject a supplier application."""
    profile = getattr(request.user, "userprofile", None)
    if not profile or not profile.is_owner or not profile.business:
        return redirect("home")

    app = get_object_or_404(
        SupplierApplication, pk=app_id, target_business=profile.business
    )

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "approve":
            app.status = "approved"
            app.reviewed_at = timezone.now()
            app.save(update_fields=["status", "reviewed_at"])

            # Auto-create supplier relationship
            SupplierRelationship.objects.get_or_create(
                business=profile.business,
                supplier=app.applicant,
                defaults={
                    "notes": f"Approved via application. Services: {app.services_offered[:100]}"
                },
            )
            django_messages.success(
                request, f"{app.applicant.name} approved as supplier!"
            )
        elif action == "reject":
            app.status = "rejected"
            app.reviewed_at = timezone.now()
            app.save(update_fields=["status", "reviewed_at"])
            django_messages.info(request, f"{app.applicant.name} application rejected.")
        return redirect("supplier_applications")

    # Calculate applicant's performance score
    from .performance import score_supplier

    supplier_perf = score_supplier(app.applicant, buyer_business=profile.business)

    avg_rating = Feedback.objects.filter(
        to_business=app.applicant,
        feedback_type="business_to_supplier",
    ).aggregate(avg=Avg("rating"))["avg"]

    return render(
        request,
        "procurement/review_application.html",
        {
            "app": app,
            "avg_rating": avg_rating,
            "supplier_perf": supplier_perf,
        },
    )


# ── SUPPLIER: BROWSE BUSINESSES TO SUPPLY ────────────────────────────────────


@login_required
def browse_businesses(request):
    """
    A registered business can browse other businesses on the platform
    and apply to become their supplier.
    """
    profile = getattr(request.user, "userprofile", None)
    if not profile or not profile.business:
        return redirect("home")

    businesses = Business.objects.exclude(pk=profile.business.pk).select_related(
        "business_type", "county"
    )

    query = request.GET.get("q", "").strip()
    category_id = request.GET.get("category", "")
    county_id = request.GET.get("county", "")

    if query:
        businesses = businesses.filter(
            Q(name__icontains=query) | Q(business_type__name__icontains=query)
        )
    if category_id:
        businesses = businesses.filter(business_type_id=category_id)
    if county_id:
        businesses = businesses.filter(county_id=county_id)

    # Mark which businesses the user has already applied to
    applied_ids = set(
        SupplierApplication.objects.filter(applicant=profile.business).values_list(
            "target_business_id", flat=True
        )
    )
    # Mark which are already supplier relationships
    supplier_ids = set(
        SupplierRelationship.objects.filter(supplier=profile.business).values_list(
            "business_id", flat=True
        )
    )

    from core.models import County

    categories = BusinessType.objects.all()
    counties = County.objects.all()

    return render(
        request,
        "procurement/browse_businesses.html",
        {
            "businesses": businesses,
            "categories": categories,
            "counties": counties,
            "query": query,
            "selected_category": category_id,
            "selected_county": county_id,
            "applied_ids": applied_ids,
            "supplier_ids": supplier_ids,
        },
    )
