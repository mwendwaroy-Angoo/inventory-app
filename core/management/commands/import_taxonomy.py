import csv
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Category


class Command(BaseCommand):
    help = 'Import taxonomy CSV into Category model. Preview mode by default; use --commit to apply.'

    def add_arguments(self, parser):
        parser.add_argument('csvpath', type=str, help='Path to taxonomy CSV file')
        parser.add_argument('--commit', action='store_true', help='Write changes to the database')

    def handle(self, *args, **options):
        path = options['csvpath']
        commit = options['commit']

        try:
            with open(path, newline='', encoding='utf-8') as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
        except FileNotFoundError:
            raise CommandError(f'File not found: {path}')

        required = {'Level1', 'Level2', 'Level3', 'SuggestedCode'}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise CommandError(f'Missing required columns: {missing}')

        codes = [r.get('SuggestedCode', '').strip() for r in rows]
        dupes = {c for c in codes if c and codes.count(c) > 1}
        if dupes:
            self.stdout.write(self.style.ERROR(f'Duplicate SuggestedCode values in CSV: {dupes}'))
            return

        create_count = 0
        update_count = 0
        unmapped = []

        if commit:
            self.stdout.write('Committing taxonomy to DB...')

        for r in rows:
            code = (r.get('SuggestedCode') or '').strip()
            lvl1 = (r.get('Level1') or '').strip()
            lvl2 = (r.get('Level2') or '').strip() or None
            lvl3 = (r.get('Level3') or '').strip() or None
            desc = (r.get('Description') or '').strip() or ''

            if not code or not lvl1:
                unmapped.append((r, 'missing code or Level1'))
                continue

            existing = Category.objects.filter(code=code).first()
            if existing:
                update_count += 1
                if commit:
                    existing.level1 = lvl1
                    existing.level2 = lvl2
                    existing.level3 = lvl3
                    existing.metadata.update({'description': desc})
                    existing.save()
            else:
                create_count += 1
                if commit:
                    Category.objects.create(code=code, level1=lvl1, level2=lvl2, level3=lvl3, metadata={'description': desc})

        self.stdout.write(self.style.SUCCESS(f'Rows processed: {len(rows)}'))
        self.stdout.write(self.style.SUCCESS(f'Creates: {create_count}  Updates: {update_count}  Errors: {len(unmapped)}'))
        if unmapped:
            self.stdout.write('Sample errors:')
            for r, err in unmapped[:10]:
                self.stdout.write(f"  {r} => {err}")
