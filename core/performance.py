"""
Performance scoring engine for riders and suppliers.

Rider Score (0–100):
    Average rating            35%
    On-time delivery rate     25%
    Item condition score      15%
    Total deliveries (exp.)   15%
    Availability bonus        10%

Supplier Score (0–100):
    Average feedback rating   30%
    Bid win rate              20%
    Price competitiveness     20%
    Proximity to buyer        15%
    Response speed            15%
"""

import math
from decimal import Decimal
from django.db.models import Avg, Count, Q

from .models import (
    RiderProfile, DeliveryRating, Order,
    Feedback, SupplierBid,
)


# ── HAVERSINE ────────────────────────────────────────────────────────────────

def _haversine_km(lat1, lon1, lat2, lon2):
    if not all([lat1, lon1, lat2, lon2]):
        return None
    R = 6371
    la1, lo1 = math.radians(float(lat1)), math.radians(float(lon1))
    la2, lo2 = math.radians(float(lat2)), math.radians(float(lon2))
    dlat, dlon = la2 - la1, lo2 - lo1
    a = math.sin(dlat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


# ── RIDER PERFORMANCE ────────────────────────────────────────────────────────

def score_rider(rider):
    """
    Calculate a composite performance score (0–100) for a rider.
    Returns dict with total score and breakdown.
    """
    ratings = DeliveryRating.objects.filter(rider=rider)
    stats = ratings.aggregate(
        avg_rating=Avg('rating'),
        avg_condition=Avg('item_condition'),
        total=Count('id'),
    )

    total_deliveries = Order.objects.filter(
        rider=rider, status='completed', delivery_mode='delivery'
    ).count()

    on_time_count = ratings.filter(on_time=True).count()
    total_rated = stats['total'] or 0

    breakdown = {}

    # 1. Average rating (35 pts)
    avg_rating = stats['avg_rating'] or 0
    rating_score = (avg_rating / 5.0) * 35 if avg_rating else 17.5  # neutral if no ratings
    breakdown['rating'] = round(rating_score, 1)

    # 2. On-time rate (25 pts)
    if total_rated > 0:
        on_time_rate = on_time_count / total_rated
        ontime_score = on_time_rate * 25
    else:
        ontime_score = 12.5  # neutral
    breakdown['on_time'] = round(ontime_score, 1)

    # 3. Item condition (15 pts)
    avg_condition = stats['avg_condition'] or 0
    condition_score = (avg_condition / 5.0) * 15 if avg_condition else 7.5
    breakdown['condition'] = round(condition_score, 1)

    # 4. Experience — total deliveries (15 pts)
    # Cap at 50 deliveries for max score
    exp_score = min(total_deliveries / 50, 1.0) * 15
    breakdown['experience'] = round(exp_score, 1)

    # 5. Availability bonus (10 pts)
    avail_score = 10 if rider.is_available else 0
    breakdown['availability'] = avail_score

    total = rating_score + ontime_score + condition_score + exp_score + avail_score
    breakdown['total'] = round(min(total, 100), 1)
    breakdown['total_deliveries'] = total_deliveries
    breakdown['total_ratings'] = total_rated
    breakdown['avg_rating'] = round(avg_rating, 1) if avg_rating else None

    return breakdown


def get_ranked_riders(business=None):
    """
    Return available riders ranked by performance score.
    If business has GPS, factor in proximity too.
    """
    riders = RiderProfile.objects.filter(is_available=True).select_related('user', 'county')
    scored = []

    for rider in riders:
        perf = score_rider(rider)
        proximity_bonus = 0

        # Proximity bonus: up to 10 extra points if business has coordinates
        if business and business.latitude and business.longitude:
            dist = _haversine_km(
                business.latitude, business.longitude,
                rider.latitude, rider.longitude,
            )
            if dist is not None:
                if dist <= 3:
                    proximity_bonus = 10
                elif dist <= 10:
                    proximity_bonus = 10 * (1 - (dist - 3) / 7)
                elif dist <= 30:
                    proximity_bonus = max(0, 5 * (1 - (dist - 10) / 20))
            elif rider.county and business.county and rider.county_id == business.county_id:
                proximity_bonus = 5  # Same county fallback

        final_score = perf['total'] + proximity_bonus
        scored.append({
            'rider': rider,
            'score': round(min(final_score, 100), 1),
            'breakdown': perf,
            'proximity_bonus': round(proximity_bonus, 1),
        })

    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored


# ── SUPPLIER PERFORMANCE ─────────────────────────────────────────────────────

def score_supplier(supplier_business, buyer_business=None):
    """
    Calculate a composite performance score (0–100) for a supplier business.
    Returns dict with total score and breakdown.
    """
    breakdown = {}

    # 1. Average feedback rating (30 pts)
    avg = Feedback.objects.filter(
        to_business=supplier_business,
        feedback_type='business_to_supplier',
    ).aggregate(avg=Avg('rating'), total=Count('id'))

    avg_rating = avg['avg'] or 0
    total_reviews = avg['total'] or 0
    rating_score = (avg_rating / 5.0) * 30 if avg_rating else 15  # neutral
    breakdown['rating'] = round(rating_score, 1)
    breakdown['avg_rating'] = round(avg_rating, 1) if avg_rating else None
    breakdown['total_reviews'] = total_reviews

    # 2. Bid win rate (20 pts)
    total_bids = SupplierBid.objects.filter(supplier=supplier_business).count()
    won_bids = SupplierBid.objects.filter(
        supplier=supplier_business, status='accepted'
    ).count()

    if total_bids > 0:
        win_rate = won_bids / total_bids
        bid_score = win_rate * 20
    else:
        bid_score = 10  # neutral
    breakdown['bid_win_rate'] = round(bid_score, 1)
    breakdown['total_bids'] = total_bids
    breakdown['won_bids'] = won_bids

    # 3. Price competitiveness (20 pts) — avg score from submitted bids
    avg_bid_score = SupplierBid.objects.filter(
        supplier=supplier_business
    ).aggregate(avg=Avg('score'))['avg']
    price_score = float(avg_bid_score or 0) / 100 * 20 if avg_bid_score else 10
    breakdown['price_competitiveness'] = round(price_score, 1)

    # 4. Proximity (15 pts) — if buyer provided
    proximity_score = 0
    if buyer_business:
        if (supplier_business.county and buyer_business.county
                and supplier_business.county_id == buyer_business.county_id):
            proximity_score += 7
        dist = _haversine_km(
            buyer_business.latitude, buyer_business.longitude,
            supplier_business.latitude, supplier_business.longitude,
        )
        if dist is not None:
            if dist <= 5:
                proximity_score += 8
            elif dist <= 20:
                proximity_score += 8 * (1 - (dist - 5) / 15)
            elif dist <= 100:
                proximity_score += max(0, 4 * (1 - (dist - 20) / 80))
        elif supplier_business.county_id == getattr(buyer_business.county, 'id', None):
            proximity_score += 4
    else:
        proximity_score = 7.5  # neutral
    breakdown['proximity'] = round(proximity_score, 1)

    # 5. Response speed (15 pts) — avg time to bid on requests
    bids_with_speed = SupplierBid.objects.filter(
        supplier=supplier_business,
    ).select_related('procurement')
    if bids_with_speed.exists():
        speeds = []
        for bid in bids_with_speed[:20]:  # sample recent 20
            hours = (bid.created_at - bid.procurement.created_at).total_seconds() / 3600
            total_hours = max(
                (bid.procurement.deadline - bid.procurement.created_at.date()).days * 24, 24
            )
            speeds.append(min(hours / total_hours, 1.0))
        avg_speed = sum(speeds) / len(speeds)
        # Faster = higher score
        speed_score = (1 - avg_speed) * 15
    else:
        speed_score = 7.5  # neutral
    breakdown['response_speed'] = round(speed_score, 1)

    total = rating_score + bid_score + price_score + proximity_score + speed_score
    breakdown['total'] = round(min(total, 100), 1)

    return breakdown


def get_ranked_suppliers(buyer_business=None, category=None):
    """
    Return suppliers ranked by performance score.
    Optionally filter by business type category.
    """
    from accounts.models import Business

    suppliers = Business.objects.all()
    if buyer_business:
        suppliers = suppliers.exclude(pk=buyer_business.pk)
    if category:
        suppliers = suppliers.filter(business_type=category)

    scored = []
    for supplier in suppliers:
        perf = score_supplier(supplier, buyer_business)
        scored.append({
            'business': supplier,
            'score': perf['total'],
            'breakdown': perf,
        })

    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored
