"""
Reusable per-business supplier price-list upload — any owner/manager can
upload their OWN supplier's Excel/CSV price list at any time and the
system parses it with the same core.catalog_classify engine used for the
one-time BAR_CATALOG enrichment. Results are stored as SupplierCatalogEntry
rows, business-scoped, coexisting with the static business_profiles.py
catalog — the "Add from Catalogue" bulk-add screen (core/views.py
catalog_bulk_add) merges both.
"""
import csv
import io
import logging

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .catalog_classify import detect_name_price_columns, classify_row
from .models import CatalogUploadBatch, SupplierCatalogEntry
from .views import get_user_profile, owner_or_manager_required

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

        # Idempotent — re-uploading the same file updates entries in place
        # instead of creating duplicates.
        SupplierCatalogEntry.objects.update_or_create(
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

    batch.rows_parsed = parsed
    batch.rows_skipped = skipped
    batch.skipped_examples = skipped_examples
    batch.save(update_fields=['rows_parsed', 'rows_skipped', 'skipped_examples'])

    messages.success(
        request,
        _('Uploaded: %(parsed)s item(s) added, %(skipped)s skipped.')
        % {'parsed': parsed, 'skipped': skipped},
    )
    return redirect('catalog_upload_batch_detail', batch_id=batch.id)


@owner_or_manager_required
def catalog_upload_batch_detail(request, batch_id):
    up = get_user_profile(request)
    batch = get_object_or_404(CatalogUploadBatch, id=batch_id, business=up.business)
    return render(request, 'core/catalog_upload_batch_detail.html', {
        'batch': batch,
        'entries': batch.entries.filter(is_active=True).order_by('name'),
    })


@owner_or_manager_required
@require_POST
def catalog_entry_deactivate(request, entry_id):
    up = get_user_profile(request)
    entry = get_object_or_404(SupplierCatalogEntry, id=entry_id, business=up.business)
    entry.is_active = False
    entry.save(update_fields=['is_active'])
    messages.success(request, _('%(name)s removed from your catalogue.') % {'name': entry.name})
    return redirect('catalog_upload_form')
