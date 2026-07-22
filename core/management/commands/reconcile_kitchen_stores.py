from django.core.management.base import BaseCommand
from django.db.models import Q

from accounts.models import Business
from core.models import Store


class Command(BaseCommand):
    """Find and fix duplicate "Kitchen" stores left behind by the bug fixed
    in commit 0228411: manage_stores lets an owner create a plain Store just
    by typing a name, with no is_kitchen checkbox — so a business could
    already have a store literally named "Kitchen" with is_kitchen=False.
    Toggling the kitchen module on then created a second, empty store
    flagged is_kitchen=True instead of adopting the existing one.

    Safe by default: dry run unless --apply is passed. Only acts when
    exactly one of the candidate stores has real items under it — that one
    is unambiguously "the real store". Anything else (both empty, both
    have items, more than two candidates) is reported and skipped rather
    than guessed.

    Usage:
        python manage.py reconcile_kitchen_stores                    # dry run, all businesses
        python manage.py reconcile_kitchen_stores --business "Monsoon Inn"
        python manage.py reconcile_kitchen_stores --business "Monsoon Inn" --apply
    """
    help = 'Find and fix duplicate "Kitchen" stores (dry run by default; pass --apply to fix).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--business', type=str, default=None,
            help='Only scan businesses whose name contains this text (case-insensitive).',
        )
        parser.add_argument(
            '--apply', action='store_true',
            help='Actually flag the real store and delete the empty duplicate. Omit to only report.',
        )

    def handle(self, *args, **options):
        apply_changes = options['apply']
        businesses = Business.objects.all()
        if options['business']:
            businesses = businesses.filter(name__icontains=options['business'])

        found_any = False

        for biz in businesses:
            candidates = list(
                Store.objects.filter(business=biz)
                .filter(Q(name__iexact='Kitchen') | Q(is_kitchen=True))
                .distinct()
            )
            if len(candidates) < 2:
                continue

            found_any = True
            self.stdout.write(self.style.WARNING(f'\n=== {biz.name} (business id={biz.id}) ==='))
            for s in candidates:
                item_count = s.items.count()
                self.stdout.write(
                    f'  Store #{s.id}: "{s.name}" is_kitchen={s.is_kitchen} items={item_count}'
                )

            with_items = [s for s in candidates if s.items.count() > 0]
            empty = [s for s in candidates if s.items.count() == 0]

            if len(with_items) != 1:
                self.stdout.write(self.style.ERROR(
                    '  -> AMBIGUOUS (expected exactly one store with items) — skipping, needs manual review.'
                ))
                continue

            real = with_items[0]
            duplicates = [s for s in empty if s.id != real.id]
            if not duplicates:
                self.stdout.write('  -> Nothing to merge (only one store has items, no empty duplicates).')
                continue

            if not real.is_kitchen:
                self.stdout.write(f'  -> Would flag #{real.id} ("{real.name}") as is_kitchen=True')
                if apply_changes:
                    real.is_kitchen = True
                    real.save(update_fields=['is_kitchen'])

            for dup in duplicates:
                self.stdout.write(f'  -> Would DELETE empty duplicate #{dup.id} ("{dup.name}")')
                if apply_changes:
                    dup.delete()

            if apply_changes:
                self.stdout.write(self.style.SUCCESS(f'  Done — "{real.name}" (#{real.id}) is now the kitchen store.'))

        if not found_any:
            self.stdout.write('No duplicate Kitchen-like stores found.')
        elif not apply_changes:
            self.stdout.write(self.style.WARNING(
                '\nDry run only — nothing was changed. Re-run with --apply to fix the cases above.'
            ))
