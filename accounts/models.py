from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from core.models import BusinessType, County, SubCounty, Ward  # updated
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver as signal_receiver
class Business(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='owned_businesses', null=True, blank=True)
    name = models.CharField(max_length=255, unique=True)
    business_type = models.ForeignKey(BusinessType, on_delete=models.PROTECT, null=True, blank=True)
    county = models.ForeignKey(County, on_delete=models.PROTECT, null=True, blank=True)
    sub_county = models.ForeignKey(SubCounty, on_delete=models.PROTECT, null=True, blank=True)  # renamed
    ward = models.ForeignKey(Ward, on_delete=models.PROTECT, null=True, blank=True)  # new
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class UserProfile(models.Model):
    ROLE_CHOICES = [
        ('owner', 'Owner'),
        ('staff', 'Staff'),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    business = models.ForeignKey(Business, on_delete=models.CASCADE,
                                  related_name='users', null=True, blank=True)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='staff')
    phone = models.CharField(max_length=20, blank=True)  # ← add this

    def __str__(self):
        return f"{self.user.username} ({self.business.name if self.business else 'No Business'}) - {self.role}"

    @property
    def is_owner(self):
        return self.role == 'owner'

    @property
    def is_staff_member(self):
        return self.role == 'staff'


# Safely save profile if it exists — does NOT auto-create
@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    try:
        instance.userprofile.save()
    except UserProfile.DoesNotExist:
        pass

@signal_receiver(user_logged_in)
def on_user_login(sender, request, user, **kwargs):
    try:
        profile = user.userprofile
    except UserProfile.DoesNotExist:
        return
    try:
        if profile.business and not profile.is_owner:
            from core.notifications import notify_staff_login
            notify_staff_login(user, profile.business, 'logged in')
    except Exception:
        pass

@signal_receiver(user_logged_out)
def on_user_logout(sender, request, user, **kwargs):
    if not user:
        return
    try:
        profile = user.userprofile
    except UserProfile.DoesNotExist:
        return
    try:
        if profile.business and not profile.is_owner:
            from core.notifications import notify_staff_login
            notify_staff_login(user, profile.business, 'logged out')
    except Exception:
        pass