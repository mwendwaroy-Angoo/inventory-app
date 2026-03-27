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
            u.is_staff = True
            u.is_superuser = True
            u.save()
            self.stdout.write(f'Password reset for {username}')
        except User.DoesNotExist:
            u = User.objects.create_superuser(
                username=username,
                email=email,
                password=password
            )
            self.stdout.write(f'Superuser {username} created successfully')

        # Ensure UserProfile exists with owner role
        from accounts.models import UserProfile, Business
        profile, created = UserProfile.objects.get_or_create(
            user=u,
            defaults={'role': 'owner'}
        )
        if not created:
            profile.role = 'owner'
            profile.save()
            self.stdout.write(f'Profile updated to owner for {username}')
        else:
            self.stdout.write(f'Owner profile created for {username}')