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

    Safe by default: dry run unless --apply is passed. "Activity" on a
    store is Items + Shifts + KegBarrels + BarTabs + StockTakes — every
    model that CASCADEs on Store deletion — not just Items, so a store
    can't be misjudged "empty" just because it happens to have no items
    right now but real shift/tab history. Two outcomes are safe to act on
    automatically:
      - exactly one candidate has any activity → that one is unambiguously
        the real store;
      - NONE of the candidates have any activity at all → there is nothing
        to lose either way, so the one already flagged is_kitchen=True is
        kept (it's what the live app is already using) and the rest are
        deleted.
    Anything else (more than one candidate has real activity) is reported
    and skipped rather than guessed.

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

    def _activity_count(self, store):
        # None of Shift.store / KegBarrel.store / BarTab.store set an
        # explicit related_name, so Django's default reverse accessors
        # apply (shift_set / kegbarrel_set / bartab_set) — verified
        # directly against core/models.py, not guessed.
        return (
            store.items.count()
            + store.shift_set.count()
            + store.kegbarrel_set.count()
            + store.bartab_set.count()
            + store.stock_takes.count()
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
            counts = {}
            for s in candidates:
                counts[s.id] = self._activity_count(s)
                self.stdout.write(
                    f'  Store #{s.id}: "{s.name}" is_kitchen={s.is_kitchen} '
                    f'items={s.items.count()} total_activity={counts[s.id]}'
                )

            with_activity = [s for s in candidates if counts[s.id] > 0]
            empty = [s for s in candidates if counts[s.id] == 0]

            if len(with_activity) > 1:
                self.stdout.write(self.style.ERROR(
                    '  -> AMBIGUOUS (more than one store has real activity) — skipping, needs manual review.'
                ))
                continue

            if len(with_activity) == 1:
                real = with_activity[0]
            else:
                # Nothing anywhere — keep whichever is already flagged as the
                # live kitchen store (or the oldest, if somehow neither is).
                flagged = [s for s in candidates if s.is_kitchen]
                real = flagged[0] if flagged else min(candidates, key=lambda s: s.id)

            duplicates = [s for s in empty if s.id != real.id]
            if not duplicates:
                self.stdout.write('  -> Nothing to merge (no empty duplicates alongside the real store).')
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
