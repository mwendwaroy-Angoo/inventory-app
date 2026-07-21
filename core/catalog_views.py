"""
Liquor/Spirits Catalogue — reusable per-business supplier price-list
upload, plus the "Add from Catalogue" bulk-add screen that consumes it.

Any owner/manager can upload their OWN supplier's Excel/CSV price list at
any time; the system parses it with the same core.catalog_classify engine
used for the one-time BAR_CATALOG enrichment. Results are stored as
SupplierCatalogEntry rows, business-scoped, coexisting with the static
business_profiles.py catalog — catalog_bulk_add() below merges both into
one pick list so an owner can create several items in a single request
instead of the item form's one-at-a-time flow.
"""
import csv
import io
import json
import logging
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db import transaction as db_transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .business_profiles import get_profile
from .catalog_classify import (
    detect_name_price_columns, classify_row, find_catalog_match_candidates,
)
from .models import (
    CatalogUploadBatch, Item, ItemPortionPreset, Store, SupplierCatalogEntry,
    SupplierCatalogEntryPriceLog,
)
from .views import get_user_profile, owner_or_manager_required, _resolve_category

logger = logging.getLogger(__name__)

_SKIPPED_EXAMPLES_CAP = 30


def _rows_from_upload(f):
    """Returns a 2D list of raw cell values from an uploaded .xlsx/.xls/.csv
    file object. Raises ValueError for an unsupported/unreadable file."""
    name = (f.name or '').lower()
    if name.endswith('.csv'):
        text = io.TextIOWrapper(f, encoding='utf-8-sig', errors='ignore')
        return [row for row in csv.reader(text)]

    try:
        import openpyxl
    except ImportError:
        raise ValueError('Excel support is unavailable on this server.')
    try:
        wb = openpyxl.load_workbook(f, data_only=True)
    except Exception as exc:
        raise ValueError(f'Could not read this file as Excel: {exc}')
    ws = wb.active
    return [list(r) for r in ws.iter_rows(values_only=True)]


@owner_or_manager_required
def catalog_upload_form(request):
    up = get_user_profile(request)
    business = up.business
    batches = CatalogUploadBatch.objects.filter(business=business).order_by('-created_at')[:20]
    entries = SupplierCatalogEntry.objects.filter(business=business, is_active=True).order_by('name')
    return render(request, 'core/catalog_upload_form.html', {
        'batches': batches,
        'entries': entries,
    })


@owner_or_manager_required
@require_POST
def catalog_upload_process(request):
    up = get_user_profile(request)
    business = up.business

    f = request.FILES.get('price_list')
    if not f:
        messages.error(request, _('Please choose a file to upload.'))
        return redirect('catalog_upload_form')

    try:
        rows = _rows_from_upload(f)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect('catalog_upload_form')

    header_idx, name_col, price_col = detect_name_price_columns(rows)
    if header_idx is None or name_col is None or price_col is None:
        messages.error(request, _('Could not find a product name and price column in this file.'))
        return redirect('catalog_upload_form')

    data_rows = rows[header_idx + 1:]
    batch = CatalogUploadBatch.objects.create(
        business=business, uploaded_by=request.user,
        original_filename=f.name or '', rows_total=len(data_rows),
    )

    parsed = 0
    skipped = 0
    price_changes = 0
    skipped_examples = []

    for row in data_rows:
        raw_name = row[name_col] if name_col < len(row) else None
        raw_price = row[price_col] if price_col < len(row) else None
        if raw_name is None and raw_price is None:
            continue
        entry = classify_row(raw_name, raw_price)
        if not entry:
            skipped += 1
            if len(skipped_examples) < _SKIPPED_EXAMPLES_CAP:
                skipped_examples.append(str(raw_name) if raw_name is not None else '')
            continue

        # Look up the OLD cost_price before update_or_create overwrites it —
        # otherwise a re-upload silently loses the previous price with no
        # record it ever changed. Only existing (not created) entries are
        # candidates for a price-change log — a brand-new entry has nothing
        # to compare against.
        existing = SupplierCatalogEntry.objects.filter(
            business=business, raw_name=entry['raw_name'],
        ).first()
        old_cost_price = existing.cost_price if existing else None

        # Idempotent — re-uploading the same file updates entries in place
        # instead of creating duplicates.
        catalog_entry, created = SupplierCatalogEntry.objects.update_or_create(
            business=business, raw_name=entry['raw_name'],
            defaults={
                'source_upload': batch,
                'name': entry['name'],
                'unit': entry.get('unit', ''),
                'volume_ml': entry.get('volume_ml'),
                'category': entry.get('category', ''),
                'cost_price': entry.get('cost_price'),
                'default_reorder_level': entry.get('default_reorder_level', 0),
                'default_reorder_quantity': entry.get('default_reorder_quantity', 0),
                'presets_json': entry.get('presets', []),
                'is_active': True,
            },
        )
        parsed += 1

        new_cost_price = catalog_entry.cost_price
        if (
            not created and old_cost_price is not None and new_cost_price is not None
            and old_cost_price != new_cost_price
        ):
            SupplierCatalogEntryPriceLog.objects.create(
                entry=catalog_entry, business=business, source_upload=batch,
                previous_cost_price=old_cost_price, cost_price=new_cost_price,
            )
            price_changes += 1

    batch.rows_parsed = parsed
    batch.rows_skipped = skipped
    batch.skipped_examples = skipped_examples
    batch.save(update_fields=['rows_parsed', 'rows_skipped', 'skipped_examples'])

    if price_changes:
        messages.success(
            request,
            _('Uploaded: %(parsed)s item(s) added, %(skipped)s skipped, '
              '%(changes)s price change(s) detected.')
            % {'parsed': parsed, 'skipped': skipped, 'changes': price_changes},
        )
    else:
        messages.success(
            request,
            _('Uploaded: %(parsed)s item(s) added, %(skipped)s skipped.')
            % {'parsed': parsed, 'skipped': skipped},
        )
    return redirect('catalog_upload_batch_detail', batch_id=batch.id)


def _variance_rows_for_batch(business, batch):
    """Unresolved price-change log rows from this batch, each paired with
    its matched Item(s) — exact via Item.source_catalog_entry when
    available, else ranked fuzzy-name suggestions (core.catalog_classify)
    for the owner to confirm. Never auto-picks a fuzzy match."""
    logs = (
        SupplierCatalogEntryPriceLog.objects
        .filter(source_upload=batch, applied=False, dismissed=False)
        .select_related('entry')
        .order_by('entry__name')
    )
    if not logs:
        return []

    unlinked_by_id = {
        i.id: i for i in Item.objects.filter(business=business, source_catalog_entry__isnull=True)
    }
    name_pairs = [(iid, i.description) for iid, i in unlinked_by_id.items()]

    rows = []
    for log in logs:
        matched_items = list(
            Item.objects.filter(business=business, source_catalog_entry=log.entry)
        )
        candidates = []
        if not matched_items and name_pairs:
            ranked = find_catalog_match_candidates(log.entry.name, name_pairs)
            candidates = [
                {'item': unlinked_by_id[iid], 'score': int(round(score * 100))}
                for iid, _name, score in ranked
            ]
        rows.append({
            'log': log,
            'matched_items': matched_items,
            'candidates': candidates,
        })
    return rows


@owner_or_manager_required
def catalog_upload_batch_detail(request, batch_id):
    up = get_user_profile(request)
    business = up.business
    batch = get_object_or_404(CatalogUploadBatch, id=batch_id, business=business)
    return render(request, 'core/catalog_upload_batch_detail.html', {
        'batch': batch,
        'entries': batch.entries.filter(is_active=True).order_by('name'),
        'variance_rows': _variance_rows_for_batch(business, batch),
    })


@owner_or_manager_required
@require_POST
def catalog_variance_apply(request, log_id):
    """Hand a detected supplier price change off to Add Transaction —
    the resolve half of the detected-variance cause, WITHOUT writing
    Item.cost_price directly.

    Add Transaction's Receipt flow is the one designed, audited place a
    cost price actually changes: it computes landed cost (unit price +
    delivery fee), creates a real stock-in Transaction, notifies the owner,
    and already shows its own variance pill comparing the entered price
    against the item's previous cost. This view must never bypass that —
    a silent field write here would be an orphaned cost change with no
    stock movement behind it, and could fight a real receipt recorded
    through the normal flow. So this only ever: (a) resolves which Item
    the catalogue entry refers to (linking source_catalog_entry so a fuzzy
    match becomes exact for next time), and (b) returns a URL to Add
    Transaction with that item + the new price pre-filled as a starting
    point — the owner still completes the actual update themselves, through
    the same Receipt flow used for every other cost change in this app.
    """
    up = get_user_profile(request)
    business = up.business
    log = get_object_or_404(SupplierCatalogEntryPriceLog, id=log_id, business=business)
    if log.is_resolved:
        return JsonResponse({'ok': False, 'error': 'Bei hii tayari imeshughulikiwa.'}, status=400)

    item_id = request.POST.get('item_id')
    if item_id:
        item = get_object_or_404(Item, id=item_id, business=business, source_catalog_entry__isnull=True)
        # Owner confirmed this fuzzy candidate is the right item — link it
        # going forward so future uploads get an exact match instead of
        # another fuzzy suggestion. Never touches cost_price.
        item.source_catalog_entry = log.entry
        item.save(update_fields=['source_catalog_entry'])
    else:
        item = Item.objects.filter(business=business, source_catalog_entry=log.entry).first()
        if not item:
            return JsonResponse({'ok': False, 'error': 'Chagua bidhaa kwanza.'}, status=400)

    log.applied = True
    log.applied_at = timezone.now()
    log.applied_by = request.user
    log.save(update_fields=['applied', 'applied_at', 'applied_by'])

    from django.urls import reverse
    redirect_url = (
        reverse('add_transaction')
        + f'?item={item.id}&suggested_cost={log.cost_price}'
    )
    return JsonResponse({'ok': True, 'redirect_url': redirect_url})


@owner_or_manager_required
@require_POST
def catalog_variance_dismiss(request, log_id):
    """Acknowledge a detected price change without updating the item's
    recorded cost — the other half of the resolve path (see
    catalog_variance_apply)."""
    up = get_user_profile(request)
    business = up.business
    log = get_object_or_404(SupplierCatalogEntryPriceLog, id=log_id, business=business)
    if log.is_resolved:
        return JsonResponse({'ok': False, 'error': 'Bei hii tayari imeshughulikiwa.'}, status=400)

    log.dismissed = True
    log.dismissed_at = timezone.now()
    log.dismissed_by = request.user
    log.save(update_fields=['dismissed', 'dismissed_at', 'dismissed_by'])

    return JsonResponse({'ok': True})


@owner_or_manager_required
@require_POST
def catalog_entry_deactivate(request, entry_id):
    up = get_user_profile(request)
    entry = get_object_or_404(SupplierCatalogEntry, id=entry_id, business=up.business)
    entry.is_active = False
    entry.save(update_fields=['is_active'])
    messages.success(request, _('%(name)s removed from your catalogue.') % {'name': entry.name})
    return redirect('catalog_upload_form')


# ── Bulk "Add from Catalogue" screen ─────────────────────────────────────

def _merged_catalog(business):
    """Static profile catalog + this business's own uploaded entries, one
    list, each item tagged with a stable key ('static:<idx>' or
    'uploaded:<id>') and source. Single source of truth for both the GET
    (picker data) and POST (server-side re-lookup, never trust client-
    supplied preset/price data for item creation) sides of the bulk-add
    screen."""
    merged = []
    static_catalog = get_profile(business).get('catalog', [])
    for i, entry in enumerate(static_catalog):
        merged.append({
            'key': f'static:{i}',
            'source': 'static',
            'name': entry.get('name', ''),
            'unit': entry.get('unit', 'Pcs'),
            'category': entry.get('category', ''),
            'volume_ml': entry.get('volume_ml'),
            'cost_price': entry.get('cost_price'),
            'is_keg': bool(entry.get('is_keg')),
            'is_produce': bool(entry.get('is_produce')),
            'produce_mode': entry.get('produce_mode', ''),
            'presets': entry.get('presets', []),
        })
    uploaded = SupplierCatalogEntry.objects.filter(business=business, is_active=True).order_by('name')
    for e in uploaded:
        merged.append({
            'key': f'uploaded:{e.id}',
            'source': 'uploaded',
            'name': e.name,
            'unit': e.unit or 'Pcs',
            'category': e.category,
            'volume_ml': e.volume_ml,
            'cost_price': float(e.cost_price) if e.cost_price is not None else None,
            'is_keg': False,
            'is_produce': False,
            'produce_mode': '',
            'presets': e.presets_json or [],
            'default_reorder_level': e.default_reorder_level,
            'default_reorder_quantity': e.default_reorder_quantity,
        })
    return merged


@owner_or_manager_required
def catalog_bulk_add(request):
    up = get_user_profile(request)
    business = up.business

    if request.method == 'POST':
        try:
            payload = json.loads(request.body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({'ok': False, 'error': 'Invalid request.'}, status=400)

        store_id = payload.get('store_id')
        store = None
        if store_id not in (None, ''):
            try:
                store = Store.objects.filter(id=int(store_id), business=business).first()
            except (TypeError, ValueError):
                store = None
        if not store:
            return JsonResponse({'ok': False, 'error': 'Chagua duka/store kwanza.'}, status=400)

        selections = payload.get('items') or []
        if not selections:
            return JsonResponse({'ok': False, 'error': 'Hakuna bidhaa iliyochaguliwa.'}, status=400)

        catalog_by_key = {e['key']: e for e in _merged_catalog(business)}

        created_count = 0
        preset_count = 0
        with db_transaction.atomic():
            last_item = Item.objects.filter(business=business).order_by('id').last()
            next_id = (last_item.id + 1) if last_item else 1

            for sel in selections:
                key = sel.get('key')
                entry = catalog_by_key.get(key)
                if not entry:
                    continue

                try:
                    cost_price = Decimal(str(sel.get('cost_price'))) if sel.get('cost_price') not in (None, '') else None
                except InvalidOperation:
                    cost_price = None
                if cost_price is None and entry.get('cost_price') is not None:
                    cost_price = Decimal(str(entry['cost_price']))

                # Capture the exact catalogue entry this item came from — an
                # uploaded key looks like "uploaded:<SupplierCatalogEntry.id>".
                # Gives the price-variance report an exact FK match instead
                # of relying on fuzzy name matching for items created here.
                source_catalog_entry_id = None
                if entry['source'] == 'uploaded':
                    try:
                        source_catalog_entry_id = int(key.split(':', 1)[1])
                    except (ValueError, IndexError):
                        source_catalog_entry_id = None

                item = Item.objects.create(
                    business=business, store=store,
                    material_no=f"MAT-{next_id:04d}",
                    description=entry['name'],
                    unit=entry.get('unit') or 'Pcs',
                    cost_price=cost_price,
                    category=_resolve_category(entry.get('category')),
                    is_keg=entry.get('is_keg', False),
                    is_produce=entry.get('is_produce', False),
                    produce_mode=entry.get('produce_mode') or 'PORTION',
                    volume_ml=entry.get('volume_ml'),
                    reorder_level=entry.get('default_reorder_level', 0),
                    reorder_quantity=entry.get('default_reorder_quantity', 0),
                    source_catalog_entry_id=source_catalog_entry_id,
                )
                next_id += 1
                created_count += 1

                if sel.get('add_presets') and entry.get('presets'):
                    for order, preset in enumerate(entry['presets']):
                        label = preset.get('label', '')
                        # Kegs need serving_type set correctly for jug/pint
                        # tracking elsewhere in the app (bar reconciliation,
                        # cup-pool accounting) — the catalog preset dict
                        # itself doesn't carry this, so infer it from the
                        # label the same way it's typically named.
                        serving_type = 'cup'
                        if 'jug' in label.lower():
                            serving_type = 'jug'
                        elif 'pint' in label.lower():
                            serving_type = 'pint'
                        # ItemPortionPreset.price has no null option (unlike
                        # the catalog dict's 'price': None convention) —
                        # add_item's own preset loop never persists a blank
                        # price either, it skips the row entirely. Since the
                        # whole point of this toggle is to scaffold preset
                        # structure/quantity_consumed math without making the
                        # owner do it by hand, use 0 as an explicit
                        # placeholder they fill in via Edit Item, rather than
                        # silently dropping every preset.
                        ItemPortionPreset.objects.create(
                            item=item,
                            label=label,
                            price=Decimal('0'),
                            quantity_consumed=Decimal(str(preset.get('qty', 1))),
                            display_order=order,
                            serving_type=serving_type,
                        )
                        preset_count += 1

        return JsonResponse({'ok': True, 'created': created_count, 'presets_created': preset_count})

    stores = Store.objects.filter(business=business, is_kitchen=False)
    return render(request, 'core/catalog_bulk_add.html', {
        'catalog_json': json.dumps(_merged_catalog(business)),
        'stores': stores,
    })
