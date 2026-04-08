from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .forms import BusinessSignupForm, AddStaffForm, BusinessEditForm, ResetStaffPasswordForm, RiderSignupForm
from .models import Business, UserProfile
from django.http import JsonResponse
from core.models import SubCounty, Ward
from django.shortcuts import get_object_or_404


def signup(request):
    if request.method == 'POST':
        form = BusinessSignupForm(request.POST)
        if form.is_valid():
            # Create the user
            user = User.objects.create_user(
                username=form.cleaned_data['username'],
                email=form.cleaned_data['email'],
                password=form.cleaned_data['password1'],
            )

            # Create the business
            business = Business.objects.create(
            owner=user,
            name=form.cleaned_data['business_name'],
            business_type=form.cleaned_data['business_type'],
            county=form.cleaned_data['county'],
            sub_county=form.cleaned_data.get('sub_county'),  # ← updated
            ward=form.cleaned_data.get('ward'),              # ← added
            phone=form.cleaned_data.get('phone', ''),
            email=form.cleaned_data.get('email_business', ''),
            address=form.cleaned_data.get('address', ''),
            )

            # Create the profile as owner
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
            messages.success(request, f"Business '{business.name}' updated successfully.")
            return redirect('home')
    else:
        form = BusinessEditForm(instance=business)

    return render(request, 'accounts/edit_business.html', {'form': form})


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
    if request.method == 'POST':
        form = RiderSignupForm(request.POST)
        if form.is_valid():
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
            from core.models import RiderProfile
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

    return render(request, 'accounts/rider_dashboard.html', {
        'rider': rider,
        'active_orders': active_orders,
        'completed_orders': completed_orders,
    })


@login_required
def rider_toggle_availability(request):
    rider = getattr(request.user, 'rider_profile', None)
    if not rider:
        return JsonResponse({'error': 'Not a rider'}, status=403)
    rider.is_available = not rider.is_available
    rider.save(update_fields=['is_available'])
    return JsonResponse({'is_available': rider.is_available})