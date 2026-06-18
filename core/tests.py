from unittest.mock import patch, MagicMock

from django.test import TestCase

from accounts.models import Business
from core.models import Receipt
from core.mpesa import _get_urls, initiate_stk_push, query_stk_status, URLS


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
