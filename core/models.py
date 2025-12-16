from django.db import models
from django.utils import timezone

class Store(models.Model):
    name = models.CharField(max_length=100)  # e.g., SF STORE, COMPUTER STORE

    def __str__(self):
        return self.name

class Item(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='items')
    material_no = models.CharField(max_length=20, unique=True)  # e.g., 100126
    description = models.CharField(max_length=200)  # e.g., Administration Folders
    unit = models.CharField(max_length=20)  # e.g., PCS, ROLL, NO
    opening_bin_balance = models.IntegerField(default=0)
    opening_physical = models.IntegerField(default=0)
    reorder_quantity = models.IntegerField(default=0)
    reorder_level = models.IntegerField(default=0)

    def current_balance(self):
        # Calculates current stock after all transactions
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
    qty = models.IntegerField()  # Positive for Receipt, Negative for Issue/Loaned
    department = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"{self.type} {abs(self.quantity)} {self.item.unit} - {self.item.description}"