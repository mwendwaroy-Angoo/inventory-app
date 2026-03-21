from django.db import models
from django.utils import timezone

# REMOVED: from accounts.models import Business   ← this caused the circle


# ────────────────────────────────────────────────
# NEW MODELS FOR MULTI-TENANCY & FEATURES
# ────────────────────────────────────────────────

class BusinessType(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']
        verbose_name_plural = "Business Types"


class County(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class SubLocation(models.Model):
    county = models.ForeignKey(County, on_delete=models.CASCADE, related_name='sublocations')
    name = models.CharField(max_length=150)

    def __str__(self):
        return f"{self.name} ({self.county.name})"

    class Meta:
        unique_together = ['county', 'name']
        ordering = ['name']


# ────────────────────────────────────────────────
# EXISTING MODELS – WITH NEW FIELDS & RELATIONS
# ────────────────────────────────────────────────

class Store(models.Model):
    # Use string reference instead of import
    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='stores')
    name = models.CharField(max_length=100)  # e.g., SF STORE, COMPUTER STORE

    # NEW: Which business types this store is suitable for
    suitable_for_types = models.ManyToManyField(BusinessType, related_name='suitable_stores', blank=True)

    def __str__(self):
        return f"{self.name} ({self.business.name})"


class Item(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='items')
    material_no = models.CharField(max_length=20, unique=True)
    description = models.CharField(max_length=200)
    unit = models.CharField(max_length=20)
    opening_bin_balance = models.IntegerField(default=0)
    opening_physical = models.IntegerField(default=0)
    reorder_quantity = models.IntegerField(default=0)
    reorder_level = models.IntegerField(default=0)

    # NEW: Link to business (string reference)
    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='items')

    # NEW: Selling price (owner can edit)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default='KES', editable=False)

    def current_balance(self):
        total_movement = self.transactions.aggregate(models.Sum('qty'))['qty__sum'] or 0
        return self.opening_bin_balance + total_movement

    def physical_balance(self):
        total_movement = self.transactions.aggregate(models.Sum('qty'))['qty__sum'] or 0
        return self.opening_physical + total_movement

    def deficit(self):
        return max(0, self.current_balance() - self.physical_balance())

    def surplus(self):
        return max(0, self.physical_balance() - self.current_balance())

    def needs_reorder(self):
        return self.current_balance() <= self.reorder_level

    def __str__(self):
        return f"{self.material_no} - {self.description}"


class Transaction(models.Model):
    TYPE_CHOICES = [
        ('Receipt', 'Receipt'),
        ('Issue', 'Issue'),
    ]

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='transactions')
    date = models.DateField(default=timezone.now)
    doc_no = models.CharField(max_length=50, blank=True)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    qty = models.IntegerField()
    department = models.CharField(max_length=100, blank=True)

    # NEW: Link to business (string reference)
    business = models.ForeignKey('accounts.Business', on_delete=models.CASCADE, related_name='transactions')

    def __str__(self):
        return f"{self.type} {abs(self.qty)} {self.item.unit} - {self.item.description}"