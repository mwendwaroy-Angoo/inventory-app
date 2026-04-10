from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from accounts.models import UserProfile


class Command(BaseCommand):
    help = 'Find and optionally delete orphan User accounts (users without a UserProfile)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--delete',
            action='store_true',
            help='Delete the orphan users (without this flag, only lists them)',
        )

    def handle(self, *args, **options):
        orphans = User.objects.filter(userprofile__isnull=True, is_superuser=False)
        count = orphans.count()

        if count == 0:
            self.stdout.write(self.style.SUCCESS('No orphan users found.'))
            return

        self.stdout.write(f'Found {count} orphan user(s):')
        for u in orphans:
            self.stdout.write(f'  id={u.id}  username={u.username}  email={u.email}  joined={u.date_joined}')

        if options['delete']:
            orphans.delete()
            self.stdout.write(self.style.SUCCESS(f'Deleted {count} orphan user(s).'))
        else:
            self.stdout.write(self.style.WARNING(
                'Run with --delete to remove them: python manage.py cleanup_orphan_users --delete'
            ))
