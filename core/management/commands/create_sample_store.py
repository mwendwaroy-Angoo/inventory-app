from django.core.management.base import BaseCommand

from accounts.models import Business
from core.models import Store


class Command(BaseCommand):
    help = 'Create a sample Business and Store for testing imports (non-destructive).'

    def add_arguments(self, parser):
        parser.add_argument('--business-name', type=str, default='Import Test Business')

    def handle(self, *args, **options):
        name = options['business_name']
        business, created = Business.objects.get_or_create(name=name)
        store, screated = Store.objects.get_or_create(business=business, name=f'{name} - Main')
        self.stdout.write(self.style.SUCCESS(f'Business id={business.id} name="{business.name}"'))
        self.stdout.write(self.style.SUCCESS(f'Store id={store.id} name="{store.name}"'))