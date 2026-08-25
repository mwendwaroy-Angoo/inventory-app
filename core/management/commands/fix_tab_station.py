from django.core.management.base import BaseCommand

from accounts.models import Business
from core.models import BarTab


class Command(BaseCommand):
    help = (
        "Explicit, per-tab correction for the 2026-08-25 revert-to-tab wrong-drawer "
        "bug (see diagnose_reverted_tab_station for how to FIND the affected tab "
        "id(s) first). Sets BarTab.source directly — the ONLY field this touches. "
        "This is a pure display-routing field (which board's tabs drawer shows the "
        "tab); it has no effect whatsoever on money, stock, or debt figures, so this "
        "is always safe to run. Deliberately requires an explicit --tab-id (one or "
        "more, comma-separated) and --station rather than guessing which station is "
        "'correct' for a historical tab — only a human who remembers which board the "
        "correction was actually made from can know that for sure."
    )

    def add_arguments(self, parser):
        parser.add_argument('--business', type=str, required=True, help='Business name substring.')
        parser.add_argument('--tab-id', type=str, required=True, help='Comma-separated BarTab id(s).')
        parser.add_argument('--station', type=str, required=True, choices=['bar', 'kitchen', 'qs'])
        parser.add_argument('--dry-run', action='store_true', help='Preview without saving.')

    def handle(self, *args, **options):
        businesses = Business.objects.filter(name__icontains=options['business'])
        if not businesses.exists():
            self.stdout.write(self.style.ERROR('No matching business.'))
            return

        try:
            tab_ids = [int(x.strip()) for x in options['tab_id'].split(',') if x.strip()]
        except ValueError:
            self.stdout.write(self.style.ERROR('--tab-id must be a comma-separated list of numeric ids.'))
            return

        dry_run = options['dry_run']
        station = options['station']

        for tab_id in tab_ids:
            tab = BarTab.objects.filter(id=tab_id, business__in=businesses).first()
            if not tab:
                self.stdout.write(self.style.ERROR(f"tab#{tab_id}: not found for this business."))
                continue
            if tab.source == station:
                self.stdout.write(f"tab#{tab_id} ({tab.customer_name}): already source='{station}' — nothing to do.")
                continue
            self.stdout.write(
                f"tab#{tab_id} ({tab.customer_name}, status={tab.status}): "
                f"source '{tab.source}' -> '{station}'"
            )
            if not dry_run:
                tab.source = station
                tab.save(update_fields=['source'])

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — nothing saved."))
        else:
            self.stdout.write(self.style.SUCCESS("Done."))
