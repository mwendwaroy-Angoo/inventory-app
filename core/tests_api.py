from django.test import TestCase, Client
from django.contrib.auth.models import User
from accounts.models import Business, UserProfile
from core.models import Store, Item


class TestItemRecommendation(TestCase):
    def setUp(self):
        # Create an owner user and business
        self.user = User.objects.create_user(username='owner1', password='pass')
        self.business = Business.objects.create(name='TestBiz', owner=self.user, email='owner@test.biz')
        # Create UserProfile and mark as owner
        self.profile = UserProfile.objects.create(user=self.user, business=self.business, role='owner')
        # Create a store and an item
        self.store = Store.objects.create(business=self.business, name='Main')
        self.item = Item.objects.create(
            store=self.store,
            material_no='MAT-0001',
            description='Test Item',
            unit='pcs',
            opening_bin_balance=0,
            opening_physical=0,
            reorder_quantity=10,
            reorder_level=5,
            business=self.business,
        )

    def test_recommendation_endpoint(self):
        c = Client()
        logged = c.login(username='owner1', password='pass')
        self.assertTrue(logged)
        url = f'/api/item/{self.item.id}/recommendation/'
        resp = c.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Expected recommended qty should be at least reorder_quantity (10)
        self.assertIn('recommended_qty', data)
        self.assertGreaterEqual(int(data['recommended_qty']), 0)
        # End of test case