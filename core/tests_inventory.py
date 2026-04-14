from django.test import TestCase
from django.utils import timezone
from datetime import timedelta

from accounts.models import Business
from core.models import Store, Item, Transaction, PurchaseOrder, PurchaseOrderLine


class InventorySupplyChainTests(TestCase):
    def setUp(self):
        # Business and store
        self.business = Business.objects.create(name='Test Biz')
        self.store = Store.objects.create(business=self.business, name='Main')

        # Item with small opening balance so recommended qty will be > 0
        self.item = Item.objects.create(
            store=self.store,
            material_no='MAT-TEST-001',
            description='Test Item',
            unit='pcs',
            opening_bin_balance=20,
            opening_physical=20,
            reorder_quantity=10,
            reorder_level=5,
            business=self.business,
            lead_time_days=5,
            safety_days=2,
            cost_price=50.0,
            selling_price=80.0,
        )

        # Create 30 days of issue transactions: 2 units per day -> avg_daily = 2
        today = timezone.now().date()
        for d in range(1, 31):
            Transaction.objects.create(
                item=self.item,
                date=today - timedelta(days=d),
                type='Issue',
                qty=-2,
                business=self.business,
            )

    def test_avg_daily_issues(self):
        avg = self.item.avg_daily_issues(window_days=30)
        # We recorded 2 units/day over 30 days
        self.assertAlmostEqual(avg, 2.0, places=3)

    def test_lead_time_and_rop(self):
        ltd = self.item.lead_time_demand()
        self.assertEqual(ltd, 10)  # 2 * 5
        ss = self.item.safety_stock()
        self.assertEqual(ss, 4)  # 2 * 2
        rop = self.item.reorder_point()
        self.assertEqual(rop, 14)  # 10 + 4

    def test_recommended_order_and_on_order(self):
        # With opening_bin_balance 20 and 30 days of -2 per day -> current_balance = 20 - 60 = -40
        self.assertEqual(self.item.current_balance(), -40)

        # recommended should be target_stock - (current_balance + on_order)
        target = self.item.target_stock()  # rop + reorder_quantity = 14 + 10 = 24
        rec = self.item.recommended_order_qty()
        expected = max(self.item.reorder_quantity, target - (self.item.current_balance() + 0))
        self.assertEqual(rec, expected)

        # Create a draft PO with 5 units on order
        po = PurchaseOrder.objects.create(business=self.business, status='ordered')
        PurchaseOrderLine.objects.create(po=po, item=self.item, quantity_ordered=5, quantity_received=0, unit_price=self.item.cost_price)

        # on_order should reflect the 5 units
        self.assertEqual(self.item.on_order(), 5)

        # recommended should now consider on_order
        rec_with_on_order = self.item.recommended_order_qty()
        expected2 = max(self.item.reorder_quantity, target - (self.item.current_balance() + 5))
        self.assertEqual(rec_with_on_order, expected2)
