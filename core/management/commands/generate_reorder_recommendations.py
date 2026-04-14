from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Item
from core import notifications
from accounts.models import Business


class Command(BaseCommand):
    help = 'Generate reorder recommendations for all businesses (prints recommendations).'

    def add_arguments(self, parser):
        parser.add_argument('--create-draft', action='store_true', help='Create draft POs for recommended items')

    def handle(self, *args, **options):
        create = options.get('create_draft', False)
        businesses = Business.objects.all()
        for b in businesses:
            items = Item.objects.filter(business=b).order_by('material_no')
            recs = []
            for item in items:
                try:
                    qty = item.recommended_order_qty()
                except Exception:
                    qty = 0
                if qty and qty > 0:
                    recs.append((item, qty))

            if not recs:
                continue

            self.stdout.write(self.style.MIGRATE_HEADING(f"Business: {b.name} ({b.id})"))
            for it, q in recs:
                self.stdout.write(f"  - {it.material_no} | {it.description} -> Recommend: {q}")

            # Create notifications (and optionally draft PO)
            try:
                created_po = notifications.notify_reorder_recommendations(b, create_draft=create)
                if created_po:
                    self.stdout.write(self.style.SUCCESS(f"  Draft PO-{created_po.id} created with {created_po.lines.count()} lines"))
            except Exception:
                self.stdout.write(self.style.WARNING('  Failed to create notifications or draft PO — see logs'))

        self.stdout.write(self.style.SUCCESS('Done.'))
