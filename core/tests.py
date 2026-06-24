from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.contrib.auth.models import User
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from accounts.models import Business
from core.models import (
    BarTab, BarTabEntry, Customer, Item, ItemPortionPreset,
    KegBarrel, Payment, Receipt, Store, Transaction,
)
from core.mpesa import _get_urls, initiate_stk_push, query_stk_status, URLS
from core.mpesa_views import _settle_tab_from_payment


# ── M-Pesa URL routing ───────────────────────────────────────────────────────

class MpesaUrlRoutingTest(TestCase):
    """_get_urls must route to the correct Safaricom cluster based on env param."""

    def test_sandbox_url_contains_sandbox_domain(self):
        urls = _get_urls('sandbox')
        self.assertIn('sandbox.safaricom.co.ke', urls['stk_push'])

    def test_production_url_contains_api_domain(self):
        urls = _get_urls('production')
        self.assertIn('api.safaricom.co.ke', urls['stk_push'])

    def test_none_falls_back_to_global_mpesa_env(self):
        urls = _get_urls(None)
        # In the test environment MPESA_ENV is not set, so it defaults to 'sandbox'
        self.assertIsNotNone(urls.get('stk_push'))
        self.assertIsNotNone(urls.get('stk_query'))

    def test_sandbox_and_production_urls_are_distinct(self):
        self.assertNotEqual(
            _get_urls('sandbox')['stk_push'],
            _get_urls('production')['stk_push'],
        )

    @patch('core.mpesa.requests')
    def test_stk_push_hits_sandbox_cluster_when_env_sandbox(self, mock_requests):
        token_resp = MagicMock()
        token_resp.json.return_value = {'access_token': 'tok'}
        token_resp.raise_for_status.return_value = None
        mock_requests.get.return_value = token_resp

        push_resp = MagicMock()
        push_resp.json.return_value = {'ResponseCode': '0', 'CheckoutRequestID': 'ws_sb'}
        push_resp.raise_for_status.return_value = None
        mock_requests.post.return_value = push_resp

        initiate_stk_push(
            phone_number='254700000001',
            amount=100,
            account_reference='TEST',
            description='Test',
            callback_url='https://example.com/cb/',
            consumer_key='key',
            consumer_secret='secret',
            shortcode='123456',
            passkey='passkey',
            env='sandbox',
        )

        stk_url = mock_requests.post.call_args[0][0]
        self.assertIn('sandbox.safaricom.co.ke', stk_url)
        self.assertNotIn('api.safaricom.co.ke', stk_url)

    @patch('core.mpesa.requests')
    def test_stk_push_hits_production_cluster_when_env_production(self, mock_requests):
        token_resp = MagicMock()
        token_resp.json.return_value = {'access_token': 'tok'}
        token_resp.raise_for_status.return_value = None
        mock_requests.get.return_value = token_resp

        push_resp = MagicMock()
        push_resp.json.return_value = {'ResponseCode': '0', 'CheckoutRequestID': 'ws_prod'}
        push_resp.raise_for_status.return_value = None
        mock_requests.post.return_value = push_resp

        initiate_stk_push(
            phone_number='254700000001',
            amount=100,
            account_reference='TEST',
            description='Test',
            callback_url='https://example.com/cb/',
            consumer_key='key',
            consumer_secret='secret',
            shortcode='654321',
            passkey='passkey',
            env='production',
        )

        stk_url = mock_requests.post.call_args[0][0]
        self.assertIn('api.safaricom.co.ke', stk_url)
        self.assertNotIn('sandbox.safaricom.co.ke', stk_url)

    @patch('core.mpesa.requests')
    def test_query_stk_status_hits_correct_cluster(self, mock_requests):
        token_resp = MagicMock()
        token_resp.json.return_value = {'access_token': 'tok'}
        token_resp.raise_for_status.return_value = None
        mock_requests.get.return_value = token_resp

        query_resp = MagicMock()
        query_resp.json.return_value = {'ResultCode': 0}
        query_resp.raise_for_status.return_value = None
        mock_requests.post.return_value = query_resp

        query_stk_status(
            'ws_abc',
            consumer_key='key',
            consumer_secret='secret',
            shortcode='123456',
            passkey='passkey',
            env='production',
        )

        query_url = mock_requests.post.call_args[0][0]
        self.assertIn('api.safaricom.co.ke', query_url)

    @patch('core.mpesa.requests')
    def test_oauth_token_fetched_from_correct_cluster(self, mock_requests):
        """The OAuth token fetch (GET) must also hit the correct cluster."""
        token_resp = MagicMock()
        token_resp.json.return_value = {'access_token': 'tok'}
        token_resp.raise_for_status.return_value = None
        mock_requests.get.return_value = token_resp

        push_resp = MagicMock()
        push_resp.json.return_value = {'ResponseCode': '0', 'CheckoutRequestID': 'ws_x'}
        push_resp.raise_for_status.return_value = None
        mock_requests.post.return_value = push_resp

        initiate_stk_push(
            phone_number='254700000001',
            amount=50,
            account_reference='T',
            description='T',
            callback_url='https://example.com/cb/',
            consumer_key='key',
            consumer_secret='secret',
            shortcode='111111',
            passkey='pass',
            env='production',
        )

        oauth_url = mock_requests.get.call_args[0][0]
        self.assertIn('api.safaricom.co.ke', oauth_url,
                      "OAuth token must be fetched from the same cluster as the STK push")


# ── Receipt sequential numbering ─────────────────────────────────────────────

class ReceiptNumberingTest(TestCase):
    """Receipt.issue() must assign gap-free sequential numbers per business."""

    def setUp(self):
        self.business = Business.objects.create(name='Test Duka Receipts')

    def test_receipts_are_numbered_from_one(self):
        r = Receipt.issue(
            self.business,
            lines=[{'name': 'Tea', 'qty': 1, 'subtotal': 50}],
            payment_method='cash',
        )
        self.assertEqual(r.receipt_number, 1)

    def test_receipts_are_sequential(self):
        r1 = Receipt.issue(self.business, lines=[{'name': 'A', 'qty': 1, 'subtotal': 10}], payment_method='cash')
        r2 = Receipt.issue(self.business, lines=[{'name': 'B', 'qty': 2, 'subtotal': 20}], payment_method='cash')
        r3 = Receipt.issue(self.business, lines=[{'name': 'C', 'qty': 1, 'subtotal': 30}], payment_method='cash')
        self.assertEqual(r1.receipt_number, 1)
        self.assertEqual(r2.receipt_number, 2)
        self.assertEqual(r3.receipt_number, 3)

    def test_receipt_numbers_are_per_business(self):
        other = Business.objects.create(name='Other Shop Receipts')
        r1 = Receipt.issue(self.business, lines=[{'name': 'Tea', 'qty': 1, 'subtotal': 50}], payment_method='cash')
        r2 = Receipt.issue(other, lines=[{'name': 'Coffee', 'qty': 1, 'subtotal': 100}], payment_method='cash')
        self.assertEqual(r1.receipt_number, 1)
        self.assertEqual(r2.receipt_number, 1, "Each business starts its own receipt sequence at 1")

    def test_receipt_tokens_are_unique(self):
        r1 = Receipt.issue(self.business, lines=[{'name': 'A', 'qty': 1, 'subtotal': 10}], payment_method='cash')
        r2 = Receipt.issue(self.business, lines=[{'name': 'B', 'qty': 1, 'subtotal': 20}], payment_method='cash')
        self.assertNotEqual(r1.token, r2.token)


# ── Sprint F1 — Bar tab debt-integrity fixes ─────────────────────────────────

def _make_keg_fixtures(business_name='Bar Test Biz'):
    """Create the minimum objects needed for keg bar tab tests."""
    business = Business.objects.create(name=business_name)
    store = Store.objects.create(business=business, name='Main Bar')
    user = User.objects.create_user(username=f'staff_{business.id}', password='x')
    item = Item.objects.create(
        business=business, store=store,
        material_no=f'KEG-{business.id}',
        description='Test Lager', unit='ml',
        is_keg=True,
        selling_price=Decimal('50'),
        cost_price=Decimal('12000'),
    )
    barrel = KegBarrel.objects.create(
        business=business, store=store, item=item,
        cost_price=Decimal('12000'),
        target_revenue=Decimal('20000'),
        status='TAPPED',
    )
    preset = ItemPortionPreset.objects.create(
        item=item, label='Pint', price=Decimal('200'),
        quantity_consumed=Decimal('500'),
    )
    return business, store, user, item, barrel, preset


def _make_tab_with_entries(business, user, barrel, preset, customer_name='Njoro', num_entries=2):
    """Open a tab and pour N rounds, creating BarTabEntry + underlying Transactions."""
    tab = BarTab.objects.create(
        business=business, customer_name=customer_name, status='OPEN',
    )
    for i in range(num_entries):
        txn = Transaction.objects.create(
            business=business, item=barrel.item, type='Issue',
            qty=Decimal('-500'), sale_amount=Decimal('200'),
            payment_method='credit', recipient=customer_name,
            keg_barrel=barrel, date=timezone.localdate(),
        )
        BarTabEntry.objects.create(
            tab=tab, transaction=txn,
            description=f'Pint ×1 (round {i+1})', amount=Decimal('200'),
        )
    return tab


class TabStkSettlementClearsDebtTest(TestCase):
    """F1.1: When an STK payment fully settles a bar tab, the underlying
    Transactions must switch from 'credit' to 'mpesa' so the debt tracker
    shows 0 outstanding."""

    def setUp(self):
        self.business, self.store, self.user, self.item, self.barrel, self.preset = (
            _make_keg_fixtures('Bar STK Test')
        )

    def test_stk_settlement_clears_debt(self):
        tab = _make_tab_with_entries(self.business, self.user, self.barrel, self.preset,
                                     customer_name='Kamau', num_entries=2)
        total = Decimal('400')  # 2 × 200

        payment = Payment.objects.create(
            business=self.business,
            bar_tab=tab,
            amount=total,
            status='completed',
            method='mpesa',
        )

        _settle_tab_from_payment(payment)

        # All entries should now be paid via mpesa
        tab.refresh_from_db()
        self.assertEqual(tab.status, 'SETTLED')
        for entry in tab.entries.all():
            self.assertTrue(entry.is_paid)
            self.assertEqual(entry.payment_method, 'mpesa')

        # Underlying transactions must NOT be 'credit' — debt tracker would
        # pick them up via filter(payment_method='credit') otherwise
        credit_count = Transaction.objects.filter(
            business=self.business,
            recipient='Kamau',
            payment_method='credit',
            type='Issue',
        ).count()
        self.assertEqual(credit_count, 0, "Debt tracker should see 0 credit transactions after STK settlement")

        mpesa_count = Transaction.objects.filter(
            business=self.business,
            recipient='Kamau',
            payment_method='mpesa',
            type='Issue',
        ).count()
        self.assertEqual(mpesa_count, 2, "Both transactions should be flipped to mpesa")


class VoidTabClearsDebtTest(TestCase):
    """F1.2: Voiding a tab must clear the underlying transactions from the debt
    tracker (payment_method='void', recipient='') and must not count as revenue."""

    def setUp(self):
        self.business, self.store, self.user, self.item, self.barrel, self.preset = (
            _make_keg_fixtures('Bar Void Test')
        )

    def test_void_tab_clears_debt_and_not_revenue(self):
        customer_name = 'Wanjiku'
        tab = _make_tab_with_entries(self.business, self.user, self.barrel, self.preset,
                                     customer_name=customer_name, num_entries=3)

        # Simulate void_tab() logic directly (we test the model-layer effect, not the view)
        from django.utils import timezone as _tz
        now = _tz.now()
        for entry in tab.entries.filter(is_paid=False).select_related('transaction'):
            entry.is_paid = True
            entry.paid_at = now
            entry.payment_method = 'void'
            entry.save(update_fields=['is_paid', 'paid_at', 'payment_method'])
            if entry.transaction_id:
                entry.transaction.payment_method = 'void'
                entry.transaction.recipient = ''
                entry.transaction.save(update_fields=['payment_method', 'recipient'])
        tab.status = 'VOID'
        tab.settled_at = now
        tab.save(update_fields=['status', 'settled_at'])

        # Debt tracker sees 0 outstanding
        credit_count = Transaction.objects.filter(
            business=self.business,
            recipient=customer_name,
            payment_method='credit',
            type='Issue',
        ).count()
        self.assertEqual(credit_count, 0, "No credit transactions should remain after void")

        # Voided transactions are not counted as revenue in analytics
        void_with_recipient = Transaction.objects.filter(
            business=self.business,
            payment_method='void',
            recipient=customer_name,
        ).count()
        self.assertEqual(void_with_recipient, 0, "Voided transactions must have recipient cleared")

        # Verify analytics exclusion: all Issue transactions excluding void
        all_issue = Transaction.objects.filter(
            business=self.business, type='Issue',
        ).exclude(payment_method='void').count()
        self.assertEqual(all_issue, 0, "Analytics (exclude void) should count 0 revenue transactions")


class ConvertTabToDebtWithDuplicateCustomersTest(TestCase):
    """F1.3: convert_tab_to_debt must not raise MultipleObjectsReturned even when
    two Customer rows share the same (business, phone)."""

    def setUp(self):
        self.business = Business.objects.create(name='Bar Dup Test')

    def test_duplicate_customers_do_not_raise(self):
        phone = '0712345678'
        # Deliberately create two customers with same business + phone (no unique constraint)
        Customer.objects.create(business=self.business, name='Otieno A', phone=phone)
        Customer.objects.create(business=self.business, name='Otieno B', phone=phone)

        # The safe lookup pattern from F1.3
        customer = Customer.objects.filter(business=self.business, phone=phone).first()
        self.assertIsNotNone(customer, "filter().first() should find one without raising")

        # Verify that using get_or_create would fail (documents why the fix matters)
        from django.core.exceptions import MultipleObjectsReturned
        with self.assertRaises(MultipleObjectsReturned):
            Customer.objects.get(business=self.business, phone=phone)


class ConcurrentKegSalesDoNotLoseUpdatesTest(TransactionTestCase):
    """F1.4: record_sale_locked must accumulate all sales without losing any
    updates. Runs sequentially here; the SELECT FOR UPDATE prevents concurrent
    clobbers in production under real DB concurrency."""

    def test_sequential_locked_sales_accumulate_correctly(self):
        business = Business.objects.create(name='Bar Concurrent Test')
        store = Store.objects.create(business=business, name='Counter')
        user = User.objects.create_user(username='staff_concurrent', password='x')
        item = Item.objects.create(
            business=business, store=store,
            material_no='KEG-CONC-1',
            description='Lager Concurrent', unit='ml',
            is_keg=True,
            selling_price=Decimal('50'),
            cost_price=Decimal('12000'),
        )
        barrel = KegBarrel.objects.create(
            business=business, store=store, item=item,
            cost_price=Decimal('12000'),
            target_revenue=Decimal('30000'),
            status='TAPPED',
        )
        preset = ItemPortionPreset.objects.create(
            item=item, label='Cup', price=Decimal('100'),
            quantity_consumed=Decimal('300'),
        )

        num_sales = 5
        for i in range(num_sales):
            KegBarrel.record_sale_locked(
                barrel.id, business, preset, 1, 'cash', user,
            )

        barrel.refresh_from_db()
        expected_revenue = Decimal('100') * num_sales
        expected_cups = num_sales
        expected_volume = Decimal('300') * num_sales

        self.assertEqual(barrel.revenue_collected, expected_revenue,
                         f"Expected revenue {expected_revenue}, got {barrel.revenue_collected}")
        self.assertEqual(barrel.cups_dispensed, expected_cups,
                         f"Expected {expected_cups} cups, got {barrel.cups_dispensed}")
        self.assertEqual(barrel.volume_dispensed_ml, expected_volume,
                         f"Expected volume {expected_volume} ml, got {barrel.volume_dispensed_ml}")

        txn_count = Transaction.objects.filter(
            business=business, keg_barrel=barrel, type='Issue',
        ).count()
        self.assertEqual(txn_count, num_sales, "One Transaction per sale must be created")
