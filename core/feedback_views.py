"""
Feedback & review views.

Customer → Business:
    - Leave review after an order (public, no login needed)
    - View business reviews (public)

Business Owner → Supplier:
    - Rate a supplier (login required, owner only)
    - View feedback given/received
"""

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages as django_messages
from django.db.models import Avg, Count

from .models import Feedback, Order, SupplierRelationship


# ── CUSTOMER → BUSINESS FEEDBACK ─────────────────────────────────────────────

def leave_customer_feedback(request, order_number):
    """Customer leaves feedback for a business after an order."""
    order = get_object_or_404(Order, order_number=order_number)

    # Check if feedback already exists for this order
    if Feedback.objects.filter(order=order, feedback_type='customer_to_business').exists():
        return render(request, 'feedback/already_submitted.html', {'order': order})

    if request.method == 'POST':
        rating = request.POST.get('rating', '')
        comment = request.POST.get('comment', '').strip()

        try:
            rating_val = int(rating)
            if not 1 <= rating_val <= 5:
                raise ValueError
        except (ValueError, TypeError):
            django_messages.error(request, 'Please select a rating between 1 and 5.')
            return redirect('leave_customer_feedback', order_number=order_number)

        Feedback.objects.create(
            feedback_type='customer_to_business',
            order=order,
            to_business=order.business,
            customer_name=order.customer_name,
            customer_phone=order.customer_phone,
            rating=rating_val,
            comment=comment,
        )
        return render(request, 'feedback/thank_you.html', {'order': order})

    return render(request, 'feedback/customer_feedback.html', {'order': order})


def business_reviews(request, business_id):
    """Public view: all customer reviews for a business."""
    from accounts.models import Business
    business = get_object_or_404(Business, pk=business_id)

    reviews = Feedback.objects.filter(
        to_business=business,
        feedback_type='customer_to_business',
    )

    stats = reviews.aggregate(
        avg_rating=Avg('rating'),
        total=Count('id'),
    )

    # Rating breakdown
    breakdown = {}
    for star in range(1, 6):
        breakdown[star] = reviews.filter(rating=star).count()

    return render(request, 'feedback/business_reviews.html', {
        'business': business,
        'reviews': reviews,
        'stats': stats,
        'breakdown': breakdown,
    })


# ── BUSINESS OWNER → SUPPLIER FEEDBACK ───────────────────────────────────────

@login_required
def supplier_feedback(request, link_id):
    """Owner: leave feedback for a supplier."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.is_owner or not profile.business:
        return redirect('home')

    link = get_object_or_404(SupplierRelationship, pk=link_id, business=profile.business)

    if request.method == 'POST':
        rating = request.POST.get('rating', '')
        comment = request.POST.get('comment', '').strip()

        try:
            rating_val = int(rating)
            if not 1 <= rating_val <= 5:
                raise ValueError
        except (ValueError, TypeError):
            django_messages.error(request, 'Please select a rating between 1 and 5.')
            return redirect('supplier_feedback', link_id=link_id)

        Feedback.objects.create(
            feedback_type='business_to_supplier',
            from_business=profile.business,
            to_business=link.supplier,
            rating=rating_val,
            comment=comment,
        )
        django_messages.success(request, f'Feedback submitted for {link.supplier.name}.')
        return redirect('supplier_list')

    # Existing feedback from this business to this supplier
    existing = Feedback.objects.filter(
        from_business=profile.business,
        to_business=link.supplier,
        feedback_type='business_to_supplier',
    ).order_by('-created_at')

    return render(request, 'feedback/supplier_feedback.html', {
        'link': link,
        'existing_feedback': existing,
    })


@login_required
def my_feedback(request):
    """Owner: view all feedback received (from customers + from business partners)."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or not profile.business:
        return redirect('home')

    customer_feedback = Feedback.objects.filter(
        to_business=profile.business,
        feedback_type='customer_to_business',
    )
    supplier_feedback_qs = Feedback.objects.filter(
        to_business=profile.business,
        feedback_type='business_to_supplier',
    )

    customer_stats = customer_feedback.aggregate(avg=Avg('rating'), total=Count('id'))
    supplier_stats = supplier_feedback_qs.aggregate(avg=Avg('rating'), total=Count('id'))

    tab = request.GET.get('tab', 'customer')

    return render(request, 'feedback/my_feedback.html', {
        'customer_feedback': customer_feedback,
        'supplier_feedback': supplier_feedback_qs,
        'customer_stats': customer_stats,
        'supplier_stats': supplier_stats,
        'tab': tab,
    })
