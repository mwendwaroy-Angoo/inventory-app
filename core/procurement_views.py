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
from django.contrib import messages as django_messages
from django.utils import timezone
from django.db.models import Avg, Q

from accounts.models import Business
from .models import (
    ProcurementRequest, SupplierBid, SupplierApplication,
    SupplierRelationship, Feedback, BusinessType,
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
    a = math.sin(dlat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlon / 2) ** 2
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
    score = Decimal('0')
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
        buyer.latitude, buyer.longitude,
        supplier.latitude, supplier.longitude,
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
        feedback_type='business_to_supplier',
    ).aggregate(avg=Avg('rating'))['avg']

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
        (procurement.deadline - procurement.created_at.date()).days * 24
    ) if procurement.deadline > procurement.created_at.date() else 24

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

    return min(score, Decimal('100'))


def score_all_bids(procurement):
    """Re-score all bids for a procurement request and save."""
    for bid in procurement.bids.all():
        bid.score = score_bid(bid, procurement)
        bid.save(update_fields=['score'])


# ── OWNER: PROCUREMENT REQUESTS ──────────────────────────────────────────────

@login_required
def procurement_list_owner(request):
    """Owner view: all their procurement requests."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.is_owner or not profile.business:
        return redirect('home')

    requests = ProcurementRequest.objects.filter(business=profile.business)

    status_filter = request.GET.get('status', '')
    if status_filter:
        requests = requests.filter(status=status_filter)

    return render(request, 'procurement/my_requests.html', {
        'requests': requests,
        'status_filter': status_filter,
    })


@login_required
def create_procurement(request):
    """Owner: create a new procurement request."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.is_owner or not profile.business:
        return redirect('home')

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        category_id = request.POST.get('category', '')
        budget_min = request.POST.get('budget_min', '') or None
        budget_max = request.POST.get('budget_max', '') or None
        deadline = request.POST.get('deadline', '')

        if not title or not description or not deadline:
            django_messages.error(request, 'Title, description, and deadline are required.')
            return redirect('create_procurement')

        pr = ProcurementRequest.objects.create(
            business=profile.business,
            title=title,
            description=description,
            category_id=category_id if category_id else None,
            budget_min=budget_min,
            budget_max=budget_max,
            deadline=deadline,
        )
        django_messages.success(request, f'Procurement "{pr.title}" created.')
        return redirect('procurement_list_owner')

    categories = BusinessType.objects.all()
    return render(request, 'procurement/create_procurement.html', {
        'categories': categories,
    })


@login_required
def procurement_detail(request, pk):
    """View a procurement request + bids (owner or supplier perspective)."""
    procurement = get_object_or_404(ProcurementRequest, pk=pk)
    profile = getattr(request.user, 'userprofile', None)

    is_owner = (
        profile and profile.business
        and profile.business == procurement.business
        and profile.is_owner
    )

    bids = procurement.bids.select_related('supplier__county', 'supplier__business_type')
    if is_owner:
        bids = bids.order_by('-score', 'amount')
    else:
        bids = bids.filter(supplier=profile.business) if profile and profile.business else bids.none()

    # Check if current user's business already bid
    already_bid = False
    if profile and profile.business:
        already_bid = procurement.bids.filter(supplier=profile.business).exists()

    return render(request, 'procurement/procurement_detail.html', {
        'procurement': procurement,
        'bids': bids,
        'is_owner': is_owner,
        'already_bid': already_bid,
    })


@login_required
def evaluate_bids(request, pk):
    """Owner: trigger scoring and view ranked bids."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.is_owner or not profile.business:
        return redirect('home')

    procurement = get_object_or_404(ProcurementRequest, pk=pk, business=profile.business)

    # Re-score all bids
    score_all_bids(procurement)

    if procurement.status == 'open':
        procurement.status = 'evaluating'
        procurement.save(update_fields=['status'])

    bids = procurement.bids.select_related(
        'supplier__county', 'supplier__business_type'
    ).order_by('-score', 'amount')

    # Top 3 shortlisted
    for i, bid in enumerate(bids):
        if i < 3 and bid.status == 'submitted':
            bid.status = 'shortlisted'
            bid.save(update_fields=['status'])

    return render(request, 'procurement/evaluate_bids.html', {
        'procurement': procurement,
        'bids': bids,
    })


@login_required
def award_bid(request, bid_id):
    """Owner: accept a bid → creates SupplierRelationship."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.is_owner or not profile.business:
        return redirect('home')

    bid = get_object_or_404(SupplierBid, pk=bid_id,
                            procurement__business=profile.business)

    if request.method == 'POST':
        bid.status = 'accepted'
        bid.save(update_fields=['status'])

        # Reject other bids
        bid.procurement.bids.exclude(pk=bid.pk).update(status='rejected')

        # Mark procurement as awarded
        bid.procurement.status = 'awarded'
        bid.procurement.save(update_fields=['status'])

        # Auto-create supplier relationship
        SupplierRelationship.objects.get_or_create(
            business=profile.business,
            supplier=bid.supplier,
            defaults={'notes': f'Awarded via procurement: {bid.procurement.title}'},
        )

        django_messages.success(
            request,
            f'Bid from {bid.supplier.name} accepted! They are now in your supplier list.',
        )
        return redirect('procurement_detail', pk=bid.procurement.pk)

    return render(request, 'procurement/award_bid.html', {'bid': bid})


# ── SUPPLIER: BROWSE & BID ──────────────────────────────────────────────────

@login_required
def procurement_browse(request):
    """Supplier: browse open procurement requests from other businesses."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.business:
        return redirect('home')

    procurements = ProcurementRequest.objects.filter(
        status='open',
        deadline__gte=timezone.now().date(),
    ).exclude(business=profile.business).select_related(
        'business__county', 'category',
    )

    query = request.GET.get('q', '').strip()
    category_id = request.GET.get('category', '')
    if query:
        procurements = procurements.filter(
            Q(title__icontains=query) | Q(description__icontains=query)
        )
    if category_id:
        procurements = procurements.filter(category_id=category_id)

    categories = BusinessType.objects.all()

    return render(request, 'procurement/browse.html', {
        'procurements': procurements,
        'categories': categories,
        'query': query,
        'selected_category': category_id,
    })


@login_required
def submit_bid(request, pk):
    """Supplier: submit a bid on a procurement request."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.business:
        return redirect('home')

    procurement = get_object_or_404(ProcurementRequest, pk=pk)

    if not procurement.is_accepting_bids:
        django_messages.error(request, 'This procurement is no longer accepting bids.')
        return redirect('procurement_browse')

    if procurement.business == profile.business:
        django_messages.error(request, "You cannot bid on your own procurement request.")
        return redirect('procurement_browse')

    if SupplierBid.objects.filter(procurement=procurement, supplier=profile.business).exists():
        django_messages.warning(request, 'You have already submitted a bid.')
        return redirect('procurement_detail', pk=pk)

    if request.method == 'POST':
        amount = request.POST.get('amount', '').strip()
        delivery_timeline = request.POST.get('delivery_timeline', '').strip()
        proposal = request.POST.get('proposal', '').strip()

        if not amount or not delivery_timeline or not proposal:
            django_messages.error(request, 'All fields are required.')
            return redirect('submit_bid', pk=pk)

        try:
            amount_val = Decimal(amount)
        except Exception:
            django_messages.error(request, 'Invalid amount.')
            return redirect('submit_bid', pk=pk)

        bid = SupplierBid.objects.create(
            procurement=procurement,
            supplier=profile.business,
            amount=amount_val,
            delivery_timeline=delivery_timeline,
            proposal=proposal,
        )
        # Score immediately
        bid.score = score_bid(bid, procurement)
        bid.save(update_fields=['score'])

        django_messages.success(request, 'Your bid has been submitted.')
        return redirect('procurement_detail', pk=pk)

    return render(request, 'procurement/submit_bid.html', {
        'procurement': procurement,
    })


@login_required
def my_bids(request):
    """Supplier: view all bids they've submitted."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.business:
        return redirect('home')

    bids = SupplierBid.objects.filter(
        supplier=profile.business,
    ).select_related('procurement__business', 'procurement__category')

    status_filter = request.GET.get('status', '')
    if status_filter:
        bids = bids.filter(status=status_filter)

    return render(request, 'procurement/my_bids.html', {
        'bids': bids,
        'status_filter': status_filter,
    })


# ── SUPPLIER APPLICATIONS ───────────────────────────────────────────────────

@login_required
def apply_as_supplier(request, business_id):
    """A business applies to become a supplier to another business."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.business:
        return redirect('home')

    target = get_object_or_404(Business, pk=business_id)

    if target == profile.business:
        django_messages.error(request, "You cannot apply to your own business.")
        return redirect('shop_home')

    if SupplierApplication.objects.filter(
        applicant=profile.business, target_business=target,
    ).exists():
        django_messages.warning(request, 'You have already applied to this business.')
        return redirect('shop_home')

    if request.method == 'POST':
        services = request.POST.get('services_offered', '').strip()
        cover = request.POST.get('cover_letter', '').strip()
        if not services or not cover:
            django_messages.error(request, 'All fields are required.')
            return redirect('apply_as_supplier', business_id=business_id)

        SupplierApplication.objects.create(
            applicant=profile.business,
            target_business=target,
            services_offered=services,
            cover_letter=cover,
        )
        django_messages.success(request, f'Application sent to {target.name}.')
        return redirect('shop_home')

    return render(request, 'procurement/apply_as_supplier.html', {
        'target': target,
    })


@login_required
def supplier_applications(request):
    """Owner: view incoming supplier applications."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.is_owner or not profile.business:
        return redirect('home')

    apps = SupplierApplication.objects.filter(
        target_business=profile.business,
    ).select_related('applicant__business_type', 'applicant__county')

    status_filter = request.GET.get('status', '')
    if status_filter:
        apps = apps.filter(status=status_filter)

    return render(request, 'procurement/supplier_applications.html', {
        'applications': apps,
        'status_filter': status_filter,
    })


@login_required
def review_application(request, app_id):
    """Owner: approve or reject a supplier application."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.is_owner or not profile.business:
        return redirect('home')

    app = get_object_or_404(SupplierApplication, pk=app_id, target_business=profile.business)

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'approve':
            app.status = 'approved'
            app.reviewed_at = timezone.now()
            app.save(update_fields=['status', 'reviewed_at'])

            # Auto-create supplier relationship
            SupplierRelationship.objects.get_or_create(
                business=profile.business,
                supplier=app.applicant,
                defaults={'notes': f'Approved via application. Services: {app.services_offered[:100]}'},
            )
            django_messages.success(request, f'{app.applicant.name} approved as supplier!')
        elif action == 'reject':
            app.status = 'rejected'
            app.reviewed_at = timezone.now()
            app.save(update_fields=['status', 'reviewed_at'])
            django_messages.info(request, f'{app.applicant.name} application rejected.')
        return redirect('supplier_applications')

    # Calculate applicant's average rating
    avg_rating = Feedback.objects.filter(
        to_business=app.applicant,
        feedback_type='business_to_supplier',
    ).aggregate(avg=Avg('rating'))['avg']

    return render(request, 'procurement/review_application.html', {
        'app': app,
        'avg_rating': avg_rating,
    })
