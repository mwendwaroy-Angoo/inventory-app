from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from core.models import BusinessType, County, SubLocation  # Import these from core


class Business(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='owned_businesses', null=True, blank=True)
    name = models.CharField(max_length=255, unique=True)
    
    # NEW fields for registration
    business_type = models.ForeignKey(BusinessType, on_delete=models.PROTECT, null=True, blank=True)
    county = models.ForeignKey(County, on_delete=models.PROTECT, null=True, blank=True)
    sub_location = models.ForeignKey(SubLocation, on_delete=models.PROTECT, null=True, blank=True)
    
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='users')

    def __str__(self):
        return f"{self.user.username} ({self.business.name if self.business else 'No Business'})"


# Signal: Auto-create UserProfile when a new User is created
@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        # For now, assign the first Business (placeholder) — you will change this in signup form
        business = Business.objects.first()
        if not business:
            # Create a default placeholder business if none exists
            business = Business.objects.create(name=f"Business for {instance.username}")
        UserProfile.objects.create(user=instance, business=business)


# Optional: Auto-save profile changes if needed (rarely used)
@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    instance.userprofile.save()