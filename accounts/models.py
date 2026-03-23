from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from core.models import BusinessType, County, SubCounty, Ward  # updated

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
    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        related_name='users',
        null=True,
        blank=True
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='staff')

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