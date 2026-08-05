"""
UBA §7.2 (Sprint R1) — fast onboarding via barcode scan. Two taps to a
stocked item (R1-AC1): scan → GlobalProduct hit pre-fills name/brand/pack →
owner enters selling price + qty only. A miss creates a new GlobalProduct row
(if this business contributes) so the NEXT duka to scan it gets the free
pre-fill this one didn't.

Deliberately a separate, small endpoint rather than widening the existing
`add_item()`/`ItemForm` machinery (core/views.py, ~200 lines of owner/manager
produce-keg-kitchen-batch-preset handling) — that view is large and heavily
tested already; a barcode-scanned item is the SIMPLE case (no presets, no
yield, no keg settings) and does not need any of that complexity. The camera
scanning UI itself (html5-qrcode / BarcodeDetector) is NOT built this pass —
these two endpoints are what a future scan UI would call, and are fully
testable via direct HTTP today without it.
"""
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .market_price import lookup_global_product, record_barcode_contribution
from .models import Item, Store
from .views import owner_or_manager_required


@login_required
@require_GET
def barcode_lookup(request, barcode):
    """Always allowed — GlobalProduct is shared reference data, not any one
    business's figures (see core/market_price.py's module docstring)."""
    gp = lookup_global_product(barcode)
    if not gp:
        return JsonResponse({'found': False})
    return JsonResponse({
        'found': True,
        'name': gp.name,
        'brand': gp.brand,
        'pack_size': gp.pack_size,
        'unit': gp.unit,
        'category': gp.category,
        'is_verified': gp.is_verified,
    })


@login_required
@owner_or_manager_required
@require_POST
def add_item_by_barcode(request):
    business = request.user.userprofile.business
    barcode = (request.POST.get('barcode') or '').strip()
    description = (request.POST.get('description') or '').strip()
    store_id = request.POST.get('store_id')
    selling_price_raw = request.POST.get('selling_price', '').strip()
    opening_qty_raw = request.POST.get('opening_qty', '0').strip()

    if not barcode:
        return JsonResponse({'ok': False, 'error': 'Barcode inahitajika.'}, status=400)

    store = Store.objects.filter(id=store_id, business=business).first()
    if not store:
        return JsonResponse({'ok': False, 'error': 'Duka halikupatikana.'}, status=400)

    gp = lookup_global_product(barcode)
    if not description:
        description = gp.name if gp else ''
    if not description:
        return JsonResponse({'ok': False, 'error': 'Jina la bidhaa linahitajika.'}, status=400)

    try:
        selling_price = Decimal(selling_price_raw) if selling_price_raw else None
    except InvalidOperation:
        return JsonResponse({'ok': False, 'error': 'Bei si sahihi.'}, status=400)

    try:
        opening_qty = int(Decimal(opening_qty_raw or '0'))
    except InvalidOperation:
        opening_qty = 0

    unit = (request.POST.get('unit') or (gp.unit if gp else '') or 'pcs').strip()

    last_item = Item.objects.filter(business=business).order_by('id').last()
    next_id = (last_item.id + 1) if last_item else 1

    item = Item.objects.create(
        business=business, store=store, description=description,
        material_no=f'MAT-{next_id:04d}', unit=unit, barcode=barcode,
        selling_price=selling_price, opening_bin_balance=opening_qty,
        opening_physical=opening_qty,
        # Anza bila kuhesabu (§7.2): an opening count typed in AT creation
        # time is itself a real physical count, unlike a barcode-scanned
        # item started at an unknown/zero balance — confirm immediately
        # only when a non-zero count was actually entered.
        balance_confirmed_at=timezone.now() if opening_qty else None,
    )

    record_barcode_contribution(
        business, barcode, description,
        brand=gp.brand if gp else '', pack_size=gp.pack_size if gp else '',
        unit=unit, category=gp.category if gp else '',
    )

    return JsonResponse({
        'ok': True,
        'item_id': item.id,
        'message': f'{item.description} imeongezwa.',
        'was_known_product': bool(gp),
    })
