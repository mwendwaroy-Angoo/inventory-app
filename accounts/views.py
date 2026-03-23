from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .forms import BusinessSignupForm, AddStaffForm
from .models import Business, UserProfile


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
                sub_location=form.cleaned_data.get('sub_location'),
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

    # Only owners can add staff
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
            )

            messages.success(request, f"Staff member '{staff_user.username}' added successfully.")
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