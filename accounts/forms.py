from django import forms
from django.contrib.auth.models import User
from .models import Business
from core.models import BusinessType, County, SubCounty, Ward


class BusinessSignupForm(forms.Form):
    # Account fields
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={'placeholder': 'Choose a username'})
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={'placeholder': 'your@email.com'})
    )
    password1 = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': 'Create a password'}),
        label='Password'
    )
    password2 = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': 'Confirm your password'}),
        label='Confirm Password'
    )

    # Business fields
    business_name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={'placeholder': 'e.g. The Royal Farm'})
    )
    business_type = forms.ModelChoiceField(
        queryset=BusinessType.objects.all(),
        empty_label='-- Select Business Type --'
    )
    phone = forms.CharField(
        max_length=20,
        required=True,
        widget=forms.TextInput(attrs={'placeholder': 'e.g. 0712345678'})
    )
    email_business = forms.EmailField(
        required=False,
        label='Business Email',
        widget=forms.EmailInput(attrs={'placeholder': 'business@email.com'})
    )
    address = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'Physical address'})
    )

    # Location fields
    county = forms.ModelChoiceField(
        queryset=County.objects.all(),
        empty_label='-- Select County --'
    )
    sub_county = forms.ModelChoiceField(
        queryset=SubCounty.objects.none(),
        empty_label='-- Select Sub County --',
        required=True
    )
    ward = forms.ModelChoiceField(
        queryset=Ward.objects.none(),
        empty_label='-- Select Ward --',
        required=False
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'county' in self.data:
            try:
                county_id = int(self.data.get('county'))
                self.fields['sub_county'].queryset = SubCounty.objects.filter(
                    county_id=county_id
                ).order_by('name')
            except (ValueError, TypeError):
                pass
        if 'sub_county' in self.data:
            try:
                sub_county_id = int(self.data.get('sub_county'))
                self.fields['ward'].queryset = Ward.objects.filter(
                    sub_county_id=sub_county_id
                ).order_by('name')
            except (ValueError, TypeError):
                pass

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("Username already taken.")
        return username

    def clean_business_name(self):
        name = self.cleaned_data['business_name']
        if Business.objects.filter(name=name).exists():
            raise forms.ValidationError("A business with this name already exists.")
        return name

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get('password1')
        p2 = cleaned_data.get('password2')
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords do not match.")
        return cleaned_data


class BusinessEditForm(forms.ModelForm):
    class Meta:
        model = Business
        fields = [
            'name', 'business_type', 'phone', 'email', 'address',
            'county', 'sub_county', 'ward',
            'opening_time', 'closing_time', 'is_open_override',
            'latitude', 'longitude',
            'offers_delivery', 'delivery_radius_km', 'delivery_fee', 'min_order_amount',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Business name'}),
            'phone': forms.TextInput(attrs={'placeholder': 'e.g. 0712345678'}),
            'email': forms.EmailInput(attrs={'placeholder': 'business@email.com'}),
            'address': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Physical address'}),
            'opening_time': forms.TimeInput(attrs={'type': 'time'}),
            'closing_time': forms.TimeInput(attrs={'type': 'time'}),
            'is_open_override': forms.Select(choices=[(None, 'Use operating hours'), (True, 'Force Open'), (False, 'Force Closed')]),
            'latitude': forms.NumberInput(attrs={'step': '0.000001', 'placeholder': 'e.g. -1.2921'}),
            'longitude': forms.NumberInput(attrs={'step': '0.000001', 'placeholder': 'e.g. 36.8219'}),
            'delivery_radius_km': forms.NumberInput(attrs={'step': '0.5', 'placeholder': 'e.g. 5'}),
            'delivery_fee': forms.NumberInput(attrs={'step': '1', 'placeholder': 'e.g. 100'}),
            'min_order_amount': forms.NumberInput(attrs={'step': '1', 'placeholder': 'e.g. 200'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['business_type'].queryset = BusinessType.objects.all()
        self.fields['business_type'].empty_label = '-- Select Business Type --'
        self.fields['county'].queryset = County.objects.all()
        self.fields['county'].empty_label = '-- Select County --'
        self.fields['ward'].required = False

        if self.instance and self.instance.pk:
            if self.instance.county_id:
                self.fields['sub_county'].queryset = SubCounty.objects.filter(
                    county_id=self.instance.county_id
                ).order_by('name')
            else:
                self.fields['sub_county'].queryset = SubCounty.objects.none()
            if self.instance.sub_county_id:
                self.fields['ward'].queryset = Ward.objects.filter(
                    sub_county_id=self.instance.sub_county_id
                ).order_by('name')
            else:
                self.fields['ward'].queryset = Ward.objects.none()
        else:
            self.fields['sub_county'].queryset = SubCounty.objects.none()
            self.fields['ward'].queryset = Ward.objects.none()

        if 'county' in self.data:
            try:
                county_id = int(self.data.get('county'))
                self.fields['sub_county'].queryset = SubCounty.objects.filter(
                    county_id=county_id
                ).order_by('name')
            except (ValueError, TypeError):
                pass
        if 'sub_county' in self.data:
            try:
                sub_county_id = int(self.data.get('sub_county'))
                self.fields['ward'].queryset = Ward.objects.filter(
                    sub_county_id=sub_county_id
                ).order_by('name')
            except (ValueError, TypeError):
                pass

    def clean_name(self):
        name = self.cleaned_data['name']
        if Business.objects.filter(name=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("A business with this name already exists.")
        return name


class PaymentSettingsForm(forms.ModelForm):
    """Form for business owners to configure their M-Pesa payment receiving details."""
    class Meta:
        model = Business
        fields = [
            'mpesa_till', 'mpesa_paybill', 'mpesa_paybill_account',
            'mpesa_pochi', 'mpesa_phone', 'preferred_payment_channel',
        ]
        widgets = {
            'mpesa_till': forms.TextInput(attrs={'placeholder': 'e.g. 5XXXXXX'}),
            'mpesa_paybill': forms.TextInput(attrs={'placeholder': 'e.g. 4XXXXXX'}),
            'mpesa_paybill_account': forms.TextInput(attrs={'placeholder': 'e.g. Account Name'}),
            'mpesa_pochi': forms.TextInput(attrs={'placeholder': 'e.g. 0712345678'}),
            'mpesa_phone': forms.TextInput(attrs={'placeholder': 'e.g. 0712345678'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
        self.fields['preferred_payment_channel'].empty_label = '-- Select Preferred Channel --'

    def clean(self):
        cleaned_data = super().clean()
        channel = cleaned_data.get('preferred_payment_channel')
        if channel == 'till' and not cleaned_data.get('mpesa_till'):
            self.add_error('mpesa_till', 'Till number is required for this channel.')
        elif channel == 'paybill' and not cleaned_data.get('mpesa_paybill'):
            self.add_error('mpesa_paybill', 'Paybill number is required for this channel.')
        elif channel == 'pochi' and not cleaned_data.get('mpesa_pochi'):
            self.add_error('mpesa_pochi', 'Pochi la Biashara phone is required for this channel.')
        elif channel == 'phone' and not cleaned_data.get('mpesa_phone'):
            self.add_error('mpesa_phone', 'M-Pesa phone number is required for this channel.')
        return cleaned_data


class ResetStaffPasswordForm(forms.Form):
    password1 = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': 'New password'}),
        label='New Password'
    )
    password2 = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': 'Confirm new password'}),
        label='Confirm Password'
    )

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get('password1')
        p2 = cleaned_data.get('password2')
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords do not match.")
        return cleaned_data


class AddStaffForm(forms.Form):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={'placeholder': 'Choose a username'})
    )
    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={'placeholder': 'staff@email.com'})
    )
    first_name = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={'placeholder': 'First name'})
    )
    last_name = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Last name'})
    )
    phone = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={'placeholder': 'e.g. 0712345678'})
    )
    password1 = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': 'Create a password'}),
        label='Password'
    )
    password2 = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': 'Confirm password'}),
        label='Confirm Password'
    )

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("Username already taken.")
        return username

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get('password1')
        p2 = cleaned_data.get('password2')
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords do not match.")
        return cleaned_data


class RiderSignupForm(forms.Form):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={'placeholder': 'Choose a username'})
    )
    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={'placeholder': 'your@email.com'})
    )
    first_name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={'placeholder': 'First name'})
    )
    last_name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={'placeholder': 'Last name'})
    )
    phone = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={'placeholder': 'e.g. 0712345678'})
    )
    county = forms.ModelChoiceField(
        queryset=County.objects.all(),
        empty_label='-- Select County --',
        required=False,
    )
    vehicle_type = forms.ChoiceField(
        choices=[
            ('motorcycle', 'Motorcycle 🏍️'),
            ('bicycle', 'Bicycle 🚲'),
            ('car', 'Car 🚗'),
            ('footsubishi', 'Footsubishi (Miguu Niponye) 🚶'),
        ],
    )
    password1 = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': 'Create a password'}),
        label='Password'
    )
    password2 = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': 'Confirm password'}),
        label='Confirm Password'
    )

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("Username already taken.")
        return username

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get('password1')
        p2 = cleaned_data.get('password2')
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords do not match.")
        return cleaned_data