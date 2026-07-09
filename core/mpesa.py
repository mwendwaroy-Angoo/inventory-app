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


def _get_urls(env=None):
    return URLS.get(env or MPESA_ENV, URLS['sandbox'])


# ── AUTH ─────────────────────────────────────────────────────────────────────

def get_access_token():
    """Get OAuth access token using global (app-level) Safaricom credentials."""
    return _get_access_token_for(
        getattr(settings, 'MPESA_CONSUMER_KEY', ''),
        getattr(settings, 'MPESA_CONSUMER_SECRET', ''),
    )


def _get_access_token_for(consumer_key, consumer_secret, env=None):
    """Get OAuth access token for the given credentials (per-business or global)."""
    if not consumer_key or not consumer_secret:
        logger.error("M-Pesa credentials not configured")
        return None
    try:
        response = requests.get(
            _get_urls(env)['oauth'],
            auth=(consumer_key, consumer_secret),
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get('access_token')
    except requests.RequestException as e:
        logger.error("M-Pesa OAuth error: %s", e)
        return None


def _generate_password(shortcode=None, passkey=None):
    """Generate the base64-encoded STK Push password + timestamp."""
    _code = shortcode or getattr(settings, 'MPESA_SHORTCODE', '')
    _pass = passkey or getattr(settings, 'MPESA_PASSKEY', '')
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    raw = f"{_code}{_pass}{timestamp}"
    password = base64.b64encode(raw.encode()).decode('utf-8')
    return password, timestamp


# ── STK PUSH ─────────────────────────────────────────────────────────────────

def initiate_stk_push(phone_number, amount, account_reference, description,
                      callback_url, consumer_key=None, consumer_secret=None,
                      shortcode=None, passkey=None, use_till=True, env=None):
    """
    Initiate Lipa Na M-Pesa Online (STK Push).

    Pass per-business credentials (consumer_key, consumer_secret, shortcode,
    passkey, env) when calling on behalf of a specific business. Falls back to
    global settings when any param is omitted or blank.

    use_till=True  → TransactionType: CustomerBuyGoodsOnline  (Till / Buy Goods)
    use_till=False → TransactionType: CustomerPayBillOnline   (Paybill)
    env: 'sandbox' or 'production' — overrides global MPESA_ENV for this call.
    """
    _key    = consumer_key    or getattr(settings, 'MPESA_CONSUMER_KEY',    '')
    _secret = consumer_secret or getattr(settings, 'MPESA_CONSUMER_SECRET', '')
    _code   = shortcode       or getattr(settings, 'MPESA_SHORTCODE',       '')
    _pass   = passkey         or getattr(settings, 'MPESA_PASSKEY',         '')
    resolved_env = env or MPESA_ENV

    logger.info("STK Push using env=%s shortcode=%s", resolved_env, _code)
    access_token = _get_access_token_for(_key, _secret, env=resolved_env)
    if not access_token:
        return None

    password, timestamp = _generate_password(_code, _pass)
    txn_type = 'CustomerBuyGoodsOnline' if use_till else 'CustomerPayBillOnline'

    payload = {
        'BusinessShortCode': _code,
        'Password': password,
        'Timestamp': timestamp,
        'TransactionType': txn_type,
        'Amount': int(amount),
        'PartyA': phone_number,
        'PartyB': _code,
        'PhoneNumber': phone_number,
        'CallBackURL': callback_url,
        'AccountReference': account_reference[:12],
        'TransactionDesc': description[:13],
    }

    try:
        response = requests.post(
            _get_urls(resolved_env)['stk_push'],
            json=payload,
            headers={
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json',
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        logger.info("STK Push initiated: %s shortcode=%s env=%s", data.get('CheckoutRequestID'), _code, resolved_env)
        return data
    except requests.RequestException as e:
        logger.error("STK Push error: %s", e)
        return None


def query_stk_status(checkout_request_id, consumer_key=None, consumer_secret=None,
                     shortcode=None, passkey=None, env=None):
    """Query the status of an STK Push transaction.

    Pass per-business credentials (including env) to query against the correct
    shortcode and cluster. Falls back to global settings when params are omitted or blank.
    """
    _key    = consumer_key    or getattr(settings, 'MPESA_CONSUMER_KEY',    '')
    _secret = consumer_secret or getattr(settings, 'MPESA_CONSUMER_SECRET', '')
    _code   = shortcode       or getattr(settings, 'MPESA_SHORTCODE',       '')
    _pass   = passkey         or getattr(settings, 'MPESA_PASSKEY',         '')
    resolved_env = env or MPESA_ENV

    access_token = _get_access_token_for(_key, _secret, env=resolved_env)
    if not access_token:
        return None

    password, timestamp = _generate_password(_code, _pass)

    payload = {
        'BusinessShortCode': _code,
        'Password': password,
        'Timestamp': timestamp,
        'CheckoutRequestID': checkout_request_id,
    }

    try:
        response = requests.post(
            _get_urls(resolved_env)['stk_query'],
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

def generate_mpesa_qr(merchant_name, shortcode, trx_code, amount=None, ref_no='PAYMENT', size=300,
                      consumer_key=None, consumer_secret=None, env=None):
    """
    Generate an M-Pesa-scannable QR code via Safaricom's Dynamic QR Code API.

    Args:
        merchant_name: Business name (max 25 chars)
        shortcode: Till number (for trx_code='BG') or Paybill (for 'PB')
        trx_code: 'BG' = Buy Goods (Till), 'PB' = Pay Bill, 'SM' = Send Money
        amount: Amount in KES as int/str (optional — customer enters on phone if omitted)
        ref_no: Reference / invoice number (max 12 chars)
        size: QR image size in pixels (default 300)
        consumer_key/consumer_secret: Per-business Daraja credentials (falls back to global)
        env: 'sandbox'|'production' — overrides global MPESA_ENV for this call

    Returns:
        base64-encoded PNG string (without data: prefix) on success, or None on failure.
        Use as: <img src="data:image/png;base64,<return_value>">
    """
    _key    = consumer_key    or getattr(settings, 'MPESA_CONSUMER_KEY',    '')
    _secret = consumer_secret or getattr(settings, 'MPESA_CONSUMER_SECRET', '')
    resolved_env = env or MPESA_ENV
    access_token = _get_access_token_for(_key, _secret, env=resolved_env)
    if not access_token:
        logger.warning("QR generate: no access token (env=%s)", resolved_env)
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
            _get_urls(resolved_env)['qr_generate'],
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
            logger.info("QR generated via Daraja for shortcode %s env=%s", shortcode, resolved_env)
            return qr_image
        logger.warning("QR API returned no QRCode field: %s", data)
        return None
    except requests.RequestException as e:
        logger.warning("QR API error for shortcode %s: %s", shortcode, e)
        return None


# ── C2B URL REGISTRATION ──────────────────────────────────────────────────────

def register_c2b_url(consumer_key, consumer_secret, shortcode, confirmation_url, validation_url, env=None):
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
        env: 'sandbox' or 'production' — overrides global MPESA_ENV for this call.

    Returns:
        dict with 'success' bool and 'message' str
    """
    if not consumer_key or not consumer_secret or not shortcode:
        return {'success': False, 'message': 'Consumer Key, Consumer Secret and Shortcode are required.'}

    resolved_env = env or MPESA_ENV

    # Get access token using THIS business's own credentials
    try:
        token_url = _get_urls(resolved_env)['oauth']
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
            _get_urls(resolved_env)['register_url'],
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


# ── EMVCo MERCHANT-PRESENTED QR (client-side Path 2) ─────────────────────────

def _crc16_ccitt(data: str) -> str:
    """CRC16-CCITT (poly 0x1021, init 0xFFFF) over ASCII data string."""
    crc = 0xFFFF
    for ch in data.encode('ascii', errors='replace'):
        crc ^= ch << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return format(crc, '04X')


def _emv_tlv(tag: int, value: str) -> str:
    val = str(value)
    return f"{tag:02d}{len(val):02d}{val}"


def generate_emv_qr_string(merchant_name: str, shortcode: str, trx_code: str, amount=None) -> str:
    """
    Build an EMVCo MPMQR string for M-Pesa Kenya.

    trx_code: 'BG' = Till (Buy Goods), 'PB' = Paybill, 'SM' = Send Money
    Returns the full MPMQR string — pass directly to QRCode(text=...).
    """
    merchant_name = str(merchant_name)[:25].strip()
    shortcode = str(shortcode).strip()

    # Tag 26 — Merchant Account Information (Safaricom sub-fields)
    ma_value = (
        _emv_tlv(0, 'com.safaricom.lipa') +
        _emv_tlv(1, trx_code) +
        _emv_tlv(2, shortcode)
    )

    parts = [
        '000201',               # Payload Format Indicator v01
        '010211',               # Point of Initiation Method: 11 = static
        _emv_tlv(26, ma_value),  # Merchant Account Information
        '52040000',             # Merchant Category Code (0000 = generic)
        '5303404',              # Currency: KES (ISO 4217 numeric 404)
    ]
    if amount:
        parts.append(_emv_tlv(54, str(int(amount))))

    parts += [
        '5802KE',                      # Country Code
        _emv_tlv(59, merchant_name),   # Merchant Name
        '6009Nairobi',                 # Merchant City (9 chars)
        '6304',                        # CRC placeholder (value appended below)
    ]

    data = ''.join(parts)
    return data + _crc16_ccitt(data)


# ── PER-COUNTER RESOLVER (Sprint K2a) ────────────────────────────────────────

def resolve_mpesa_config(business, store=None):
    """Effective M-Pesa account for a sale at `store`.

    Store overrides Business when store.has_own_mpesa is True.
    Backward compatible: no store or has_own_mpesa=False → returns Business config.

    Returns a dict with keys:
        till, paybill, paybill_account, pochi,
        consumer_key, consumer_secret, passkey, environment,
        store (Store instance or None), source ('bar' | 'kitchen')
    """
    if store is not None and getattr(store, 'has_own_mpesa', False):
        return {
            'till':             (store.mpesa_till or '').strip(),
            'paybill':          (store.mpesa_paybill or '').strip(),
            'paybill_account':  (store.mpesa_paybill_account or '').strip(),
            'pochi':            (store.mpesa_pochi or '').strip(),
            'consumer_key':     store.daraja_consumer_key or business.daraja_consumer_key,
            'consumer_secret':  store.daraja_consumer_secret or business.daraja_consumer_secret,
            'passkey':          store.daraja_passkey or business.daraja_passkey,
            'environment':      (store.daraja_environment or business.daraja_environment or 'sandbox'),
            'store':            store,
            'source':           'kitchen' if getattr(store, 'is_kitchen', False) else 'bar',
        }
    return {
        'till':             (business.mpesa_till or '').strip(),
        'paybill':          (business.mpesa_paybill or '').strip(),
        'paybill_account':  (business.mpesa_paybill_account or '').strip(),
        'pochi':            (business.mpesa_pochi or '').strip(),
        'consumer_key':     business.daraja_consumer_key,
        'consumer_secret':  business.daraja_consumer_secret,
        'passkey':          business.daraja_passkey,
        'environment':      business.daraja_environment or 'sandbox',
        'store':            None,
        'source':           'kitchen' if (store is not None and getattr(store, 'is_kitchen', False)) else 'bar',
    }


def resolve_account_by_shortcode(shortcode):
    """Reverse lookup for an incoming C2B payment.

    Checks Store-level overrides (more specific) first, then falls back to Business.

    Returns (business, store_or_None, channel_str) where channel is 'till'/'paybill'/'pochi'.
    Returns (None, None, '') if not found.
    """
    from django.db.models import Q
    from accounts.models import Business
    from .models import Store

    sc = (shortcode or '').strip()
    if not sc:
        return None, None, ''

    store = (
        Store.objects
        .filter(has_own_mpesa=True)
        .filter(Q(mpesa_till=sc) | Q(mpesa_paybill=sc) | Q(mpesa_pochi=sc))
        .select_related('business')
        .first()
    )
    if store:
        if store.mpesa_till == sc:
            ch = 'till'
        elif store.mpesa_paybill == sc:
            ch = 'paybill'
        else:
            ch = 'pochi'
        return store.business, store, ch

    for field, ch in (('mpesa_till', 'till'), ('mpesa_paybill', 'paybill'), ('mpesa_pochi', 'pochi')):
        biz = Business.objects.filter(**{field: sc}).first()
        if biz:
            return biz, None, ch

    return None, None, ''


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
