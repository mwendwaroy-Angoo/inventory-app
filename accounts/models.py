from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from core.models import BusinessType, County, SubCounty, Ward  # updated
from django.contrib.auth.signals import user_logged_in, user_logged_out
import math
from django.utils import timezone


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

    # ── Operating Hours ──
    opening_time = models.TimeField(null=True, blank=True, help_text='e.g. 08:00')
    closing_time = models.TimeField(null=True, blank=True, help_text='e.g. 18:00')
    is_open_override = models.BooleanField(null=True, blank=True, default=None,
        help_text='Manual override: True=force open, False=force closed, None=use hours')

    # ── GPS Coordinates ──
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    # ── Delivery Settings ──
    offers_delivery = models.BooleanField(default=False)
    delivery_radius_km = models.DecimalField(max_digits=5, decimal_places=1, default=5, help_text='Max delivery distance in km')
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Base delivery fee in KES')
    delivery_fee_per_km = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Additional fee per km')
    min_order_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Base minimum order in KES')
    min_order_per_km = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Additional minimum per km of distance')

    # ── Payment Receiving Settings ──
    PAYMENT_CHANNEL_CHOICES = [
        ('till', 'Till Number (Buy Goods)'),
        ('paybill', 'Paybill'),
        ('pochi', 'Pochi la Biashara'),
        ('phone', 'Personal M-Pesa'),
    ]
    mpesa_till = models.CharField(max_length=20, blank=True, help_text='Lipa Na M-Pesa Till Number (Buy Goods)')
    mpesa_paybill = models.CharField(max_length=20, blank=True, help_text='Paybill Business Number')
    mpesa_paybill_account = models.CharField(max_length=50, blank=True, help_text='Paybill Account Number')
    mpesa_pochi = models.CharField(max_length=20, blank=True, help_text='Pochi la Biashara phone number')
    mpesa_phone = models.CharField(max_length=20, blank=True, help_text='Personal M-Pesa phone number for receiving')
    preferred_payment_channel = models.CharField(
        max_length=10, choices=PAYMENT_CHANNEL_CHOICES, blank=True,
        help_text='Default payment channel customers should use')

    def __str__(self):
        return self.name

    def is_open(self):
        """Check if business is currently open based on Nairobi time."""
        if self.is_open_override is not None:
            return self.is_open_override
        if not self.opening_time or not self.closing_time:
            return True  # No hours set = always open
        now = timezone.localtime(timezone.now()).time()
        if self.opening_time <= self.closing_time:
            return self.opening_time <= now <= self.closing_time
        else:
            # Overnight hours (e.g. 22:00 - 06:00)
            return now >= self.opening_time or now <= self.closing_time

    def distance_to(self, lat, lng):
        """Haversine distance in km to a given lat/lng."""
        if not self.latitude or not self.longitude or not lat or not lng:
            return None
        R = 6371  # Earth radius in km
        lat1, lon1 = math.radians(float(self.latitude)), math.radians(float(self.longitude))
        lat2, lon2 = math.radians(float(lat)), math.radians(float(lng))
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return R * 2 * math.asin(math.sqrt(a))

    def calc_delivery_fee(self, distance_km):
        """Calculate delivery fee based on distance."""
        return float(self.delivery_fee or 0) + float(self.delivery_fee_per_km or 0) * distance_km

    def calc_min_order(self, distance_km):
        """Calculate minimum order amount based on distance."""
        return float(self.min_order_amount or 0) + float(self.min_order_per_km or 0) * distance_km

    def recommend_delivery_tier(self, distance_km):
        """Return the best delivery tier for the given distance, or None."""
        tiers = self.delivery_tiers.filter(
            max_distance_km__gte=distance_km
        ).order_by('max_distance_km')
        return tiers.first()


class DeliveryTier(models.Model):
    MODE_CHOICES = [
        ('foot', '🚶 On Foot'),
        ('bicycle', '🚲 Bicycle'),
        ('boda', '🏍️ Boda Boda'),
        ('vehicle', '🚗 Vehicle'),
    ]
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='delivery_tiers')
    mode = models.CharField(max_length=15, choices=MODE_CHOICES)
    max_distance_km = models.DecimalField(max_digits=5, decimal_places=1, help_text='Max range for this mode in km')
    base_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Base fee for this mode')
    fee_per_km = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Additional fee per km')

    class Meta:
        ordering = ['max_distance_km']
        unique_together = ['business', 'mode']

    def __str__(self):
        return f"{self.get_mode_display()} — up to {self.max_distance_km}km"

    def calc_fee(self, distance_km):
        return float(self.base_fee) + float(self.fee_per_km) * distance_km


class UserProfile(models.Model):
    ROLE_CHOICES = [
        ('owner', 'Owner'),
        ('staff', 'Staff'),
        ('rider', 'Rider'),
        ('supplier', 'Supplier'),
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

    @property
    def is_rider(self):
        return self.role == 'rider'

    @property
    def is_supplier(self):
        return self.role == 'supplier'


# Safely save profile if it exists — does NOT auto-create
@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    try:
        instance.userprofile.save()
    except UserProfile.DoesNotExist:
        pass

@receiver(user_logged_in)
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

@receiver(user_logged_out)
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