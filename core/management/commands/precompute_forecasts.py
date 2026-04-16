from django.core.management.base import BaseCommand
from django.core.management import call_command


class Command(BaseCommand):
    help = 'Precompute forecasts for all businesses (calls `forecast` per business)'

    def add_arguments(self, parser):
        parser.add_argument('--source', choices=['transaction', 'order', 'both'], default='both')
        parser.add_argument('--cadence', choices=['daily', 'weekly', 'monthly'], default='daily')
        parser.add_argument('--horizon', type=int, default=30)
        parser.add_argument('--output-dir', default='forecast/output', help='Directory to write outputs')

    def handle(self, *args, **options):
        source = options['source']
        cadence = options['cadence']
        horizon = options['horizon']
        output_dir = options['output_dir']

        from django.apps import apps
        Business = apps.get_model('accounts', 'Business')

        businesses = Business.objects.all()
        for b in businesses:
            self.stdout.write(f'Precomputing forecast for business id={b.id} name={b.name}')
            try:
                call_command('forecast', '--source', source, '--cadence', cadence, '--horizon', str(horizon), '--business', str(b.id), '--output-dir', output_dir)
            except Exception as e:
                self.stderr.write(f'Error precomputing for business {b.id}: {e}')

        self.stdout.write(self.style.SUCCESS('Precompute complete'))
