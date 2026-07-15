import random
import secrets

from django.core.management.base import BaseCommand
from django.db.models import Q

from core.models import BarTab


class Command(BaseCommand):
    help = 'Backfill tab_receipt_token and tab_pin on open BarTabs missing them'

    def handle(self, *args, **options):
        tabs = BarTab.objects.filter(status='OPEN').filter(
            Q(tab_receipt_token='') | Q(tab_pin='')
        )
        count = 0
        for tab in tabs:
            changed = False
            if not tab.tab_receipt_token:
                tab.tab_receipt_token = secrets.token_urlsafe(20)
                changed = True
            if not tab.tab_pin:
                existing = set(
                    BarTab.objects.filter(business=tab.business, status='OPEN')
                    .exclude(id=tab.id)
                    .values_list('tab_pin', flat=True)
                )
                pin = str(random.randint(1000, 9999))
                while pin in existing:
                    pin = str(random.randint(1000, 9999))
                tab.tab_pin = pin
                changed = True
            if changed:
                tab.save(update_fields=['tab_receipt_token', 'tab_pin'])
                count += 1
        self.stdout.write(f'Backfilled {count} tabs.')
