from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from accounts.models import UserProfile, Business


class Command(BaseCommand):
    help = 'Creates missing UserProfile records for users without one, assigning them to their owner business as staff'

    def handle(self, *args, **kwargs):
        users_without_profile = []
        for user in User.objects.all():
            try:
                user.userprofile
            except UserProfile.DoesNotExist:
                users_without_profile.append(user)

        if not users_without_profile:
            self.stdout.write(self.style.SUCCESS('All users already have profiles.'))
            return

        # Find the real business (one that has an owner)
        owner_profile = UserProfile.objects.filter(role='owner', business__isnull=False).first()
        if owner_profile:
            business = owner_profile.business
        else:
            business = Business.objects.first()

        if not business:
            self.stdout.write(self.style.ERROR('No business found. Create a business first.'))
            return

        self.stdout.write(f'Assigning orphaned users to business: {business.name}')

        for user in users_without_profile:
            # Skip superusers — they should be owners, not staff
            if user.is_superuser:
                self.stdout.write(f'  Skipping superuser: {user.username}')
                continue

            UserProfile.objects.create(
                user=user,
                business=business,
                role='staff',
            )
            self.stdout.write(self.style.SUCCESS(f'  Created staff profile for: {user.username}'))

        self.stdout.write(self.style.SUCCESS('Done.'))
