from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from accounts.models import UserProfile, Business


class Command(BaseCommand):
    help = 'Creates missing UserProfile records for users without one, assigning them to their owner business as staff'

    def handle(self, *args, **kwargs):
        # 2026-08-11: this ran on EVERY deploy's release phase (Procfile:
        # migrate && fix_staff_profiles && reset_superuser) and used to loop
        # over every User on the whole platform, issuing one extra query per
        # user (`user.userprofile` on a cache miss hits the DB) to find the
        # handful that are actually missing a profile. On a platform with
        # many registered businesses/staff this is a real N+1 burst of
        # queries stacked on top of whatever else is happening right at
        # deploy time, on a database with a very tight CPU allowance —
        # investigated as a candidate contributor to intermittent 502s
        # correlated with deploy timing. Rewritten to one query.
        users_without_profile = list(
            User.objects.filter(userprofile__isnull=True)
        )

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
