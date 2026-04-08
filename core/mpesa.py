"""
M-Pesa Daraja API integration for Duka Mwecheche.

Supports:
- STK Push (Lipa Na M-Pesa Online) — initiate payment from customer's phone
- C2B Callbacks — receive payment confirmation from Safaricom
- Transaction status query

Requires env vars:
    MPESA_CONSUMER_KEY, MPESA_CONSUMER_SECRET,
    MPESA_SHORTCODE, MPESA_PASSKEY,
    MPESA_ENV (sandbox|production)
"""

import base64
import logging
from datetime import datetime

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# ── CONFIG ───────────────────────────────────────────────────────────────────

MPESA_ENV = getattr(settings, 'MPESA_ENV', 'sandbox')

URLS = {
    'sandbox': {
        'base': 'https://sandbox.safaricom.co.ke',
        'oauth': 'https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials',
        'stk_push': 'https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest',
        'stk_query': 'https://sandbox.safaricom.co.ke/mpesa/stkpushquery/v1/query',
        'register_url': 'https://sandbox.safaricom.co.ke/mpesa/c2b/v1/registerurl',
    },
    'production': {
        'base': 'https://api.safaricom.co.ke',
        'oauth': 'https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials',
        'stk_push': 'https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest',
        'stk_query': 'https://api.safaricom.co.ke/mpesa/stkpushquery/v1/query',
        'register_url': 'https://api.safaricom.co.ke/mpesa/c2b/v1/registerurl',
    },
}


def _get_urls():
    return URLS.get(MPESA_ENV, URLS['sandbox'])


# ── AUTH ─────────────────────────────────────────────────────────────────────

def get_access_token():
    """Get OAuth access token from Safaricom."""
    consumer_key = getattr(settings, 'MPESA_CONSUMER_KEY', '')
    consumer_secret = getattr(settings, 'MPESA_CONSUMER_SECRET', '')

    if not consumer_key or not consumer_secret:
        logger.error("M-Pesa credentials not configured")
        return None

    try:
        response = requests.get(
            _get_urls()['oauth'],
            auth=(consumer_key, consumer_secret),
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get('access_token')
    except requests.RequestException as e:
        logger.error("M-Pesa OAuth error: %s", e)
        return None


def _generate_password():
    """Generate the base64-encoded password for STK Push."""
    shortcode = getattr(settings, 'MPESA_SHORTCODE', '')
    passkey = getattr(settings, 'MPESA_PASSKEY', '')
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    raw = f"{shortcode}{passkey}{timestamp}"
    password = base64.b64encode(raw.encode()).decode('utf-8')
    return password, timestamp


# ── STK PUSH ─────────────────────────────────────────────────────────────────

def initiate_stk_push(phone_number, amount, account_reference, description,
                      callback_url):
    """
    Initiate Lipa Na M-Pesa Online (STK Push).

    Args:
        phone_number: Customer phone in 2547XXXXXXXX format
        amount: Amount in KES (integer)
        account_reference: e.g. "ORDER-123"
        description: Transaction description
        callback_url: Full URL for M-Pesa to POST results to

    Returns:
        dict with response data or None on failure
    """
    access_token = get_access_token()
    if not access_token:
        return None

    password, timestamp = _generate_password()
    shortcode = getattr(settings, 'MPESA_SHORTCODE', '')

    payload = {
        'BusinessShortCode': shortcode,
        'Password': password,
        'Timestamp': timestamp,
        'TransactionType': 'CustomerPayBillOnline',
        'Amount': int(amount),
        'PartyA': phone_number,
        'PartyB': shortcode,
        'PhoneNumber': phone_number,
        'CallBackURL': callback_url,
        'AccountReference': account_reference[:12],  # Max 12 chars
        'TransactionDesc': description[:13],  # Max 13 chars
    }

    try:
        response = requests.post(
            _get_urls()['stk_push'],
            json=payload,
            headers={
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json',
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        logger.info("STK Push initiated: %s", data.get('CheckoutRequestID'))
        return data
    except requests.RequestException as e:
        logger.error("STK Push error: %s", e)
        return None


def query_stk_status(checkout_request_id):
    """Query the status of an STK Push transaction."""
    access_token = get_access_token()
    if not access_token:
        return None

    password, timestamp = _generate_password()
    shortcode = getattr(settings, 'MPESA_SHORTCODE', '')

    payload = {
        'BusinessShortCode': shortcode,
        'Password': password,
        'Timestamp': timestamp,
        'CheckoutRequestID': checkout_request_id,
    }

    try:
        response = requests.post(
            _get_urls()['stk_query'],
            json=payload,
            headers={
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json',
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error("STK Query error: %s", e)
        return None


# ── PHONE FORMATTING ─────────────────────────────────────────────────────────

def format_phone_ke(phone):
    """Normalize Kenyan phone to 2547XXXXXXXX format."""
    phone = phone.strip().replace(' ', '').replace('-', '')
    if phone.startswith('+'):
        phone = phone[1:]
    if phone.startswith('0'):
        phone = '254' + phone[1:]
    if phone.startswith('7') or phone.startswith('1'):
        phone = '254' + phone
    return phone
