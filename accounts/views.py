from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from .forms import BusinessSignupForm, AddStaffForm, BusinessEditForm, ResetStaffPasswordForm, RiderSignupForm, PaymentSettingsForm, SupplierSignupForm
from .models import Business, UserProfile
from django.http import JsonResponse
from core.models import SubCounty, Ward
from django.shortcuts import get_object_or_404


@login_required
def role_redirect(request):
    """Route users to their role-specific dashboard after login."""
    profile = getattr(request.user, 'userprofile', None)
    if profile:
        if profile.role == 'rider':
            return redirect('rider_dashboard')
        if profile.role == 'supplier':
            return redirect('supplier_dashboard')
    return redirect('home')


@login_required
def tutorial_dismiss(request):
    """Mark the tutorial as seen for the current user."""
    if request.method == 'POST':
        profile = getattr(request.user, 'userprofile', None)
        if profile:
            profile.has_seen_tutorial = True
            profile.save(update_fields=['has_seen_tutorial'])
        return JsonResponse({'ok': True})
    return JsonResponse({'error': 'POST required'}, status=405)


@login_required
def tutorial_reset(request):
    """Reset the tutorial so it shows again."""
    if request.method == 'POST':
        profile = getattr(request.user, 'userprofile', None)
        if profile:
            profile.has_seen_tutorial = False
            profile.save(update_fields=['has_seen_tutorial'])
        return JsonResponse({'ok': True, 'show': True})
    return JsonResponse({'error': 'POST required'}, status=405)


def signup(request):
    if request.user.is_authenticated:
        return redirect('role_redirect')
    if request.method == 'POST':
        form = BusinessSignupForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = User.objects.create_user(
                    username=form.cleaned_data['username'],
                    email=form.cleaned_data['email'],
                    password=form.cleaned_data['password1'],
                )
                business = Business.objects.create(
                    owner=user,
                    name=form.cleaned_data['business_name'],
                    business_type=form.cleaned_data['business_type'],
                    county=form.cleaned_data['county'],
                    sub_county=form.cleaned_data.get('sub_county'),
                    ward=form.cleaned_data.get('ward'),
                    phone=form.cleaned_data.get('phone', ''),
                    email=form.cleaned_data.get('email_business', ''),
                    address=form.cleaned_data.get('address', ''),
                )
                UserProfile.objects.create(
                    user=user,
                    business=business,
                    role='owner',
                )

            login(request, user)
            messages.success(request, f"Welcome! Your business '{business.name}' has been created.")
            return redirect('home')
    else:
        form = BusinessSignupForm()

    return render(request, 'registration/signup.html', {'form': form})


@login_required
def add_staff(request):
    try:
        user_profile = request.user.userprofile
    except Exception:
        return redirect('home')

    if not user_profile.is_owner:
        messages.error(request, "Only business owners can add staff.")
        return redirect('home')

    if request.method == 'POST':
        form = AddStaffForm(request.POST)
        if form.is_valid():
            staff_user = User.objects.create_user(
                username=form.cleaned_data['username'],
                email=form.cleaned_data.get('email', ''),
                password=form.cleaned_data['password1'],
                first_name=form.cleaned_data.get('first_name', ''),
                last_name=form.cleaned_data.get('last_name', ''),
            )

            UserProfile.objects.create(
                user=staff_user,
                business=user_profile.business,
                role='staff',
                phone=form.cleaned_data.get('phone', ''),  # ← added
            )

            messages.success(
                request,
                f"Staff member '{staff_user.username}' added successfully."
            )
            return redirect('staff_list')
    else:
        form = AddStaffForm()

    return render(request, 'accounts/add_staff.html', {'form': form})

@login_required
def staff_list(request):
    try:
        user_profile = request.user.userprofile
    except Exception:
        return redirect('home')

    if not user_profile.is_owner:
        messages.error(request, "Only business owners can view staff.")
        return redirect('home')

    staff = UserProfile.objects.filter(
        business=user_profile.business,
        role='staff'
    ).select_related('user')

    return render(request, 'accounts/staff_list.html', {'staff': staff})

def load_subcounties(request):
    county_id = request.GET.get('county_id')
    subcounties = SubCounty.objects.filter(
        county_id=county_id
    ).order_by('name').values('id', 'name')
    return JsonResponse(list(subcounties), safe=False)


def load_wards(request):
    sub_county_id = request.GET.get('sub_county_id')
    wards = Ward.objects.filter(
        sub_county_id=sub_county_id
    ).order_by('name').values('id', 'name')
    return JsonResponse(list(wards), safe=False)


@login_required
def edit_staff(request, user_id):
    try:
        user_profile = request.user.userprofile
    except Exception:
        return redirect('home')

    if not user_profile.is_owner:
        messages.error(request, "Only business owners can edit staff.")
        return redirect('home')

    staff_profile = get_object_or_404(
        UserProfile,
        user__id=user_id,
        business=user_profile.business,
        role='staff'
    )

    if request.method == 'POST':
        staff_profile.user.first_name = request.POST.get('first_name', '')
        staff_profile.user.last_name = request.POST.get('last_name', '')
        staff_profile.user.email = request.POST.get('email', '')
        staff_profile.user.save()
        staff_profile.phone = request.POST.get('phone', '')
        staff_profile.save()
        messages.success(request, f"'{staff_profile.user.username}' updated successfully.")
        return redirect('staff_list')

    return render(request, 'accounts/edit_staff.html', {'profile': staff_profile})


@login_required
def delete_staff(request, user_id):
    try:
        user_profile = request.user.userprofile
    except Exception:
        return redirect('home')

    if not user_profile.is_owner:
        messages.error(request, "Only business owners can delete staff.")
        return redirect('home')

    staff_profile = get_object_or_404(
        UserProfile,
        user__id=user_id,
        business=user_profile.business,
        role='staff'
    )

    if request.method == 'POST':
        username = staff_profile.user.username
        staff_profile.user.delete()
        messages.success(request, f"'{username}' removed successfully.")
        return redirect('staff_list')

    return render(request, 'accounts/delete_staff.html', {'profile': staff_profile})


@login_required
def edit_business(request):
    try:
        user_profile = request.user.userprofile
    except Exception:
        return redirect('home')

    if not user_profile.is_owner:
        messages.error(request, "Only business owners can edit business details.")
        return redirect('home')

    business = user_profile.business
    if not business:
        messages.error(request, "No business found.")
        return redirect('home')

    if request.method == 'POST':
        form = BusinessEditForm(request.POST, instance=business)
        if form.is_valid():
            form.save()
            # Handle delivery tiers
            _save_delivery_tiers(request, business)
            messages.success(request, f"Business '{business.name}' updated successfully.")
            return redirect('home')
    else:
        form = BusinessEditForm(instance=business)

    delivery_tiers = business.delivery_tiers.all()
    return render(request, 'accounts/edit_business.html', {
        'form': form,
        'delivery_tiers': delivery_tiers,
    })


def _save_delivery_tiers(request, business):
    """Process delivery tier inline forms from POST data."""
    from .models import DeliveryTier
    # Delete removed tiers
    existing_ids = []
    i = 0
    while True:
        mode = request.POST.get(f'tier_mode_{i}')
        if mode is None:
            break
        tier_id = request.POST.get(f'tier_id_{i}', '').strip()
        max_dist = request.POST.get(f'tier_max_km_{i}', '').strip()
        base_fee = request.POST.get(f'tier_base_fee_{i}', '').strip()
        fee_km = request.POST.get(f'tier_fee_per_km_{i}', '').strip()
        delete = request.POST.get(f'tier_delete_{i}')

        if delete:
            if tier_id:
                DeliveryTier.objects.filter(id=tier_id, business=business).delete()
            i += 1
            continue

        if mode and max_dist:
            defaults = {
                'max_distance_km': max_dist,
                'base_fee': base_fee or 0,
                'fee_per_km': fee_km or 0,
            }
            if tier_id:
                DeliveryTier.objects.filter(id=tier_id, business=business).update(mode=mode, **defaults)
                existing_ids.append(int(tier_id))
            else:
                tier = DeliveryTier.objects.create(business=business, mode=mode, **defaults)
                existing_ids.append(tier.id)
        i += 1


@login_required
def reset_staff_password(request, user_id):
    try:
        user_profile = request.user.userprofile
    except Exception:
        return redirect('home')

    if not user_profile.is_owner:
        messages.error(request, "Only business owners can reset staff passwords.")
        return redirect('home')

    staff_profile = get_object_or_404(
        UserProfile,
        user__id=user_id,
        business=user_profile.business,
        role='staff'
    )

    if request.method == 'POST':
        form = ResetStaffPasswordForm(request.POST)
        if form.is_valid():
            staff_profile.user.set_password(form.cleaned_data['password1'])
            staff_profile.user.save()
            messages.success(request, f"Password for '{staff_profile.user.username}' reset successfully.")
            return redirect('staff_list')
    else:
        form = ResetStaffPasswordForm()

    return render(request, 'accounts/reset_staff_password.html', {
        'form': form,
        'profile': staff_profile,
    })


# ── RIDER REGISTRATION ──────────────────────────────────────────────────────

def rider_signup(request):
    if request.user.is_authenticated:
        return redirect('role_redirect')
    if request.method == 'POST':
        form = RiderSignupForm(request.POST)
        if form.is_valid():
            from core.models import RiderProfile
            with transaction.atomic():
                user = User.objects.create_user(
                    username=form.cleaned_data['username'],
                    email=form.cleaned_data.get('email', ''),
                    password=form.cleaned_data['password1'],
                    first_name=form.cleaned_data['first_name'],
                    last_name=form.cleaned_data['last_name'],
                )
                UserProfile.objects.create(
                    user=user,
                    role='rider',
                    phone=form.cleaned_data['phone'],
                )
                RiderProfile.objects.create(
                    user=user,
                    phone=form.cleaned_data['phone'],
                    county=form.cleaned_data.get('county'),
                    vehicle_type=form.cleaned_data['vehicle_type'],
                )
            login(request, user)
            messages.success(request, "Welcome! You're registered as a rider.")
            return redirect('rider_dashboard')
    else:
        form = RiderSignupForm()
    return render(request, 'registration/rider_signup.html', {'form': form})


@login_required
def rider_dashboard(request):
    profile = getattr(request.user, 'userprofile', None)
    if not profile or profile.role != 'rider':
        return redirect('home')

    rider = getattr(request.user, 'rider_profile', None)
    if not rider:
        return redirect('home')

    from core.models import Order
    from core.performance import score_rider

    active_orders = Order.objects.filter(
        rider=rider,
        status__in=['confirmed', 'paid', 'ready'],
        delivery_mode='delivery',
    )
    completed_orders = Order.objects.filter(
        rider=rider,
        status='completed',
        delivery_mode='delivery',
    )[:20]

    performance = score_rider(rider)

    return render(request, 'accounts/rider_dashboard.html', {
        'rider': rider,
        'active_orders': active_orders,
        'completed_orders': completed_orders,
        'performance': performance,
    })


@login_required
def rider_toggle_availability(request):
    rider = getattr(request.user, 'rider_profile', None)
    if not rider:
        return JsonResponse({'error': 'Not a rider'}, status=403)
    rider.is_available = not rider.is_available
    rider.save(update_fields=['is_available'])
    return JsonResponse({'is_available': rider.is_available})


@login_required
def rider_active_deliveries(request):
    profile = getattr(request.user, 'userprofile', None)
    if not profile or profile.role != 'rider':
        return redirect('home')
    rider = getattr(request.user, 'rider_profile', None)
    if not rider:
        return redirect('home')

    from core.models import Order
    active_orders = Order.objects.filter(
        rider=rider,
        status__in=['confirmed', 'paid', 'ready'],
        delivery_mode='delivery',
    ).select_related('business').order_by('-created_at')

    return render(request, 'rider/active_deliveries.html', {
        'rider': rider,
        'active_orders': active_orders,
    })


@login_required
def rider_delivery_history(request):
    profile = getattr(request.user, 'userprofile', None)
    if not profile or profile.role != 'rider':
        return redirect('home')
    rider = getattr(request.user, 'rider_profile', None)
    if not rider:
        return redirect('home')

    from core.models import Order
    completed_orders = Order.objects.filter(
        rider=rider,
        status='completed',
        delivery_mode='delivery',
    ).select_related('business').order_by('-created_at')[:50]

    return render(request, 'rider/delivery_history.html', {
        'rider': rider,
        'completed_orders': completed_orders,
    })


@login_required
def rider_earnings(request):
    profile = getattr(request.user, 'userprofile', None)
    if not profile or profile.role != 'rider':
        return redirect('home')
    rider = getattr(request.user, 'rider_profile', None)
    if not rider:
        return redirect('home')

    from core.models import Order
    from django.db.models import Sum, Count

    completed = Order.objects.filter(
        rider=rider, status='completed', delivery_mode='delivery',
    )
    total_deliveries = completed.count()
    total_earnings = completed.aggregate(
        total=Sum('delivery_fee')
    )['total'] or 0

    return render(request, 'rider/earnings.html', {
        'rider': rider,
        'total_deliveries': total_deliveries,
        'total_earnings': total_earnings,
    })


# ── SUPPLIER REGISTRATION ────────────────────────────────────────────────────

def supplier_signup(request):
    if request.user.is_authenticated:
        return redirect('role_redirect')
    if request.method == 'POST':
        form = SupplierSignupForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = User.objects.create_user(
                    username=form.cleaned_data['username'],
                    email=form.cleaned_data['email'],
                    password=form.cleaned_data['password1'],
                )
                business = Business.objects.create(
                    owner=user,
                    name=form.cleaned_data['business_name'],
                    business_type=form.cleaned_data['business_type'],
                    county=form.cleaned_data['county'],
                    sub_county=form.cleaned_data.get('sub_county'),
                    ward=form.cleaned_data.get('ward'),
                    phone=form.cleaned_data.get('phone', ''),
                    email=form.cleaned_data.get('email_business', ''),
                )
                UserProfile.objects.create(
                    user=user,
                    business=business,
                    role='supplier',
                    phone=form.cleaned_data.get('phone', ''),
                )
            login(request, user)
            messages.success(request, f"Welcome! Your supply business '{business.name}' has been registered.")
            return redirect('supplier_dashboard')
    else:
        form = SupplierSignupForm()
    return render(request, 'registration/supplier_signup.html', {'form': form})


@login_required
def supplier_dashboard(request):
    profile = getattr(request.user, 'userprofile', None)
    if not profile or profile.role != 'supplier':
        return redirect('home')

    business = profile.business
    if not business:
        return redirect('home')

    from core.models import SupplierRelationship, SupplierBid, ProcurementRequest

    # Stats
    clients = SupplierRelationship.objects.filter(
        supplier=business
    ).count()
    active_bids = SupplierBid.objects.filter(
        supplier=business, status='submitted'
    ).count()
    won_bids = SupplierBid.objects.filter(
        supplier=business, status='accepted'
    ).count()
    open_requests = ProcurementRequest.objects.filter(
        status='open'
    ).exclude(business=business).count()

    # Recent bids
    recent_bids = SupplierBid.objects.filter(
        supplier=business
    ).select_related('procurement').order_by('-created_at')[:10]

    return render(request, 'supplier/dashboard.html', {
        'business': business,
        'clients': clients,
        'active_bids': active_bids,
        'won_bids': won_bids,
        'open_requests': open_requests,
        'recent_bids': recent_bids,
    })


@login_required
def supplier_clients(request):
    """List businesses this supplier is approved to supply."""
    profile = getattr(request.user, 'userprofile', None)
    if not profile or profile.role != 'supplier':
        return redirect('home')

    from core.models import SupplierRelationship
    relationships = SupplierRelationship.objects.filter(
        supplier=profile.business
    ).select_related('business')

    return render(request, 'supplier/clients.html', {
        'relationships': relationships,
    })


@login_required
def payment_settings(request):
    """Business owner configures M-Pesa receiving channels (Till/Paybill/Pochi/Phone)."""
    try:
        user_profile = request.user.userprofile
    except UserProfile.DoesNotExist:
        return redirect('home')

    if not user_profile.is_owner:
        messages.error(request, "Only business owners can manage payment settings.")
        return redirect('home')

    business = user_profile.business
    if not business:
        messages.error(request, "No business found.")
        return redirect('home')

    if request.method == 'POST':
        form = PaymentSettingsForm(request.POST, instance=business)
        if form.is_valid():
            form.save()
            messages.success(request, "Payment settings updated successfully.")
            return redirect('payment_settings')
    else:
        form = PaymentSettingsForm(instance=business)

    return render(request, 'accounts/payment_settings.html', {
        'form': form,
        'business': business,
    })