from django.core.management.base import BaseCommand
from django.contrib.auth.models import User


class Command(BaseCommand):
    help = 'Creates or resets the superuser password'

    def handle(self, *args, **kwargs):
        username = 'Roy'
        password = '#RoyAdmin2026'
        email = 'mwendwaroy@gmail.com'

        try:
            u = User.objects.get(username=username)
            u.set_password(password)
            u.save()
            self.stdout.write(f'Password reset for {username}')
        except User.DoesNotExist:
            User.objects.create_superuser(
                username=username,
                email=email,
                password=password
            )
            self.stdout.write(f'Superuser {username} created successfully')