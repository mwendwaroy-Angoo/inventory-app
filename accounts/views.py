from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .forms import BusinessSignupForm, AddStaffForm
from .models import Business, UserProfile
from django.http import JsonResponse
from core.models import SubCounty, Ward

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