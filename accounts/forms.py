from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from .models import Business, UserProfile
from core.models import BusinessType, County, SubLocation


class BusinessSignupForm(forms.Form):
    # User fields
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    password1 = forms.CharField(widget=forms.PasswordInput, label='Password')
    password2 = forms.CharField(widget=forms.PasswordInput, label='Confirm Password')

    # Business fields
    business_name = forms.CharField(max_length=255)
    business_type = forms.ModelChoiceField(queryset=BusinessType.objects.all())
    county = forms.ModelChoiceField(queryset=County.objects.all())
    sub_location = forms.ModelChoiceField(queryset=SubLocation.objects.all(), required=False)
    phone = forms.CharField(max_length=20, required=False)
    email_business = forms.EmailField(required=False, label='Business Email')
    address = forms.TextField = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}), required=False)

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


class AddStaffForm(forms.Form):
    username = forms.CharField(max_length=150)
    email = forms.EmailField(required=False)
    password1 = forms.CharField(widget=forms.PasswordInput, label='Password')
    password2 = forms.CharField(widget=forms.PasswordInput, label='Confirm Password')
    first_name = forms.CharField(max_length=100, required=False)
    last_name = forms.CharField(max_length=100, required=False)

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