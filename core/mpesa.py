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
        'qr_generate': 'https://sandbox.safaricom.co.ke/mpesa/qrcode/v1/generate',
    },
    'production': {
        'base': 'https://api.safaricom.co.ke',
        'oauth': 'https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials',
        'stk_push': 'https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest',
        'stk_query': 'https://api.safaricom.co.ke/mpesa/stkpushquery/v1/query',
        'register_url': 'https://api.safaricom.co.ke/mpesa/c2b/v1/registerurl',
        'qr_generate': 'https://api.safaricom.co.ke/mpesa/qrcode/v1/generate',
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


# ── DYNAMIC QR CODE (Path 1 — Safaricom Daraja API) ─────────────────────────

def generate_mpesa_qr(merchant_name, shortcode, trx_code, amount=None, ref_no='PAYMENT', size=300):
    """
    Generate an M-Pesa-scannable QR code via Safaricom's Dynamic QR Code API.

    Args:
        merchant_name: Business name (max 25 chars)
        shortcode: Till number (for trx_code='BG') or Paybill (for 'PB')
        trx_code: 'BG' = Buy Goods (Till), 'PB' = Pay Bill, 'SM' = Send Money
        amount: Amount in KES as int/str (optional — customer enters on phone if omitted)
        ref_no: Reference / invoice number (max 12 chars)
        size: QR image size in pixels (default 300)

    Returns:
        base64-encoded PNG string (without data: prefix) on success, or None on failure.
        Use as: <img src="data:image/png;base64,<return_value>">
    """
    access_token = get_access_token()
    if not access_token:
        logger.warning("QR generate: no access token")
        return None

    payload = {
        'MerchantName': str(merchant_name)[:25],
        'RefNo': str(ref_no)[:12],
        'Amount': str(int(amount)) if amount else '0',
        'TrxCode': trx_code,
        'CPI': str(shortcode),
        'Size': str(size),
    }

    try:
        resp = requests.post(
            _get_urls()['qr_generate'],
            json=payload,
            headers={
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json',
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        qr_image = data.get('QRCode')
        if qr_image:
            logger.info("QR generated via Daraja for shortcode %s", shortcode)
            return qr_image
        logger.warning("QR API returned no QRCode field: %s", data)
        return None
    except requests.RequestException as e:
        logger.warning("QR API error for shortcode %s: %s", shortcode, e)
        return None


# ── C2B URL REGISTRATION ──────────────────────────────────────────────────────

def register_c2b_url(consumer_key, consumer_secret, shortcode, confirmation_url, validation_url):
    """
    Register C2B callback URLs with Safaricom for a specific shortcode (Till/Paybill).

    This is a one-time call per shortcode. After registration, Safaricom will POST
    to confirmation_url whenever a customer pays to the shortcode.

    Args:
        consumer_key: Daraja Consumer Key for this shortcode's API credentials
        consumer_secret: Daraja Consumer Secret for this shortcode's API credentials
        shortcode: The Till or Paybill number
        confirmation_url: Full HTTPS URL Safaricom will POST payment confirmations to
        validation_url: Full HTTPS URL Safaricom will call before completing payment

    Returns:
        dict with 'success' bool and 'message' str
    """
    if not consumer_key or not consumer_secret or not shortcode:
        return {'success': False, 'message': 'Consumer Key, Consumer Secret and Shortcode are required.'}

    # Get access token using THIS business's own credentials
    try:
        token_url = _get_urls()['oauth']
        token_resp = requests.get(
            token_url,
            auth=(consumer_key, consumer_secret),
            timeout=30,
        )
        token_resp.raise_for_status()
        access_token = token_resp.json().get('access_token')
    except requests.RequestException as e:
        logger.error("C2B register — token error for shortcode %s: %s", shortcode, e)
        return {'success': False, 'message': f'Could not authenticate with Safaricom: {e}'}

    if not access_token:
        return {'success': False, 'message': 'Could not get access token from Safaricom. Check your credentials.'}

    payload = {
        'ShortCode': shortcode,
        'ResponseType': 'Completed',
        'ConfirmationURL': confirmation_url,
        'ValidationURL': validation_url,
    }

    try:
        resp = requests.post(
            _get_urls()['register_url'],
            json=payload,
            headers={
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json',
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info("C2B register response for shortcode %s: %s", shortcode, data)

        response_code = str(data.get('ResponseCode', ''))
        response_desc = data.get('ResponseDescription', '') or data.get('CustomerMessage', '')

        if response_code == '0':
            return {'success': True, 'message': f'Registered successfully. {response_desc}'.strip()}
        else:
            return {'success': False, 'message': f'Safaricom error {response_code}: {response_desc}'}

    except requests.RequestException as e:
        logger.error("C2B register — API error for shortcode %s: %s", shortcode, e)
        return {'success': False, 'message': f'API error: {e}'}


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
