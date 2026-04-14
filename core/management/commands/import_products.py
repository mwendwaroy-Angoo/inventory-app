import csv
from decimal import Decimal, InvalidOperation
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Category, Item, Store


class Command(BaseCommand):
    help = 'Import products CSV and map to Category. Preview by default; use --commit --store-id <id> to write.'

    def add_arguments(self, parser):
        parser.add_argument('csvpath', type=str, help='Path to products CSV file')
        parser.add_argument('--commit', action='store_true', help='Persist changes to DB')
        parser.add_argument('--store-id', type=int, help='Store id to assign imported items to (required for --commit)')

    def handle(self, *args, **options):
        path = options['csvpath']
        commit = options['commit']
        store_id = options.get('store_id')

        try:
            with open(path, newline='', encoding='utf-8') as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
        except FileNotFoundError:
            raise CommandError(f'File not found: {path}')

        required = {'sku', 'name', 'price', 'cost'}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise CommandError(f'Missing required columns: {missing}')

        if commit and not store_id:
            raise CommandError('--store-id is required when using --commit')

        store = None
        if store_id:
            store = Store.objects.filter(id=store_id).first()
            if not store:
                raise CommandError(f'Store not found: {store_id}')

        errors = []
        unmapped = []
        created = 0
        updated = 0

        for i, r in enumerate(rows, start=1):
            sku = (r.get('sku') or '').strip()
            name = (r.get('name') or '').strip()
            suggested_code = (r.get('suggested_code') or '').strip()
            lvl1 = (r.get('level1') or '').strip()
            lvl2 = (r.get('level2') or '').strip() or None
            lvl3 = (r.get('level3') or '').strip() or None
            tags = (r.get('tags') or '').strip()
            price_raw = (r.get('price') or '').strip()
            cost_raw = (r.get('cost') or '').strip()

            if not sku or not name:
                errors.append((i, sku, 'missing sku or name'))
                continue

            # parse numeric fields
            try:
                price = Decimal(price_raw) if price_raw else None
            except InvalidOperation:
                errors.append((i, sku, f'invalid price: {price_raw}'))
                continue
            try:
                cost = Decimal(cost_raw) if cost_raw else None
            except InvalidOperation:
                errors.append((i, sku, f'invalid cost: {cost_raw}'))
                continue

            # find category
            category = None
            if suggested_code:
                category = Category.objects.filter(code=suggested_code).first()
            if not category and lvl1:
                category = Category.objects.filter(level1=lvl1, level2=lvl2, level3=lvl3).first()
            if not category:
                unmapped.append((i, sku, suggested_code, lvl1))

            # preview mode - collect issues and show mapping
            if not commit:
                # just report
                continue

            # commit mode: create or update Item
            with transaction.atomic():
                item = Item.objects.filter(material_no=sku).first()
                if item:
                    # update
                    item.description = name
                    item.selling_price = price
                    item.cost_price = cost
                    if category:
                        item.category = category
                    if tags:
                        item.tags = [t.strip().lower() for t in tags.replace(';', ',').split(',') if t.strip()]
                    item.store = store
                    item.save()
                    updated += 1
                else:
                    Item.objects.create(
                        material_no=sku,
                        description=name,
                        unit=r.get('unit') or 'pcs',
                        store=store,
                        selling_price=price,
                        cost_price=cost,
                        category=category,
                        tags=[t.strip().lower() for t in tags.replace(';', ',').split(',') if t.strip()]
                    )
                    created += 1

        # summary
        self.stdout.write(self.style.SUCCESS(f'Rows: {len(rows)}'))
        self.stdout.write(self.style.SUCCESS(f'Errors: {len(errors)}  Unmapped categories: {len(unmapped)}'))
        if commit:
            self.stdout.write(self.style.SUCCESS(f'Created: {created}  Updated: {updated}'))
        else:
            if errors:
                self.stdout.write('Sample errors:')
                for e in errors[:10]:
                    self.stdout.write(str(e))
            if unmapped:
                self.stdout.write('Sample unmapped rows (row, sku, suggested_code, level1):')
                for u in unmapped[:10]:
                    self.stdout.write(str(u))
