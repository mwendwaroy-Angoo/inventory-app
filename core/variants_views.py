"""
UBA §8.2 (Sprint A1) — variant matrix creator endpoint. The actual matrix-
creator UI grid (tick sizes/colours, bulk quantity entry) is deferred, see
core/variants.py's module docstring.
"""
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .models import Store
from .variants import create_variant_matrix
from .views import owner_or_manager_required


@login_required
@owner_or_manager_required
@require_POST
def create_variant_matrix_view(request):
    up = request.user.userprofile
    business = up.business

    product_name = (request.POST.get('product_name') or '').strip()
    if not product_name:
        return JsonResponse({'ok': False, 'error': 'Jina la bidhaa linahitajika.'}, status=400)

    store = Store.objects.filter(id=request.POST.get('store_id'), business=business).first()
    if not store:
        return JsonResponse({'ok': False, 'error': 'Duka halikupatikana.'}, status=400)

    try:
        base_price = Decimal(request.POST.get('base_price', '').strip())
    except InvalidOperation:
        return JsonResponse({'ok': False, 'error': 'Bei si sahihi.'}, status=400)

    sizes = [s.strip() for s in request.POST.getlist('sizes') if s.strip()]
    colours = [c.strip() for c in request.POST.getlist('colours') if c.strip()]
    try:
        opening_qty = int(request.POST.get('opening_qty', '0') or '0')
    except ValueError:
        opening_qty = 0

    parent, children = create_variant_matrix(
        business, store, product_name, sizes, colours, base_price, opening_qty=opening_qty,
    )
    return JsonResponse({
        'ok': True, 'parent_id': parent.id,
        'children': [{'id': c.id, 'material_no': c.material_no, 'variant_label': c.variant_label} for c in children],
    })
