from django.core.management.base import BaseCommand
from django.contrib.auth.models import User


class Command(BaseCommand):
    help = 'Resets the superuser password from environment variable'

    def handle(self, *args, **kwargs):
        username = 'Roy'
        password = '#Tabbynzuki88'

        try:
            u = User.objects.get(username=username)
            u.set_password(password)
            u.save()
            self.stdout.write(f'Password reset for {username}')
        except User.DoesNotExist:
            self.stdout.write(f'User {username} not found')