from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Business
from core.models import Shift


class Command(BaseCommand):
    help = (
        "Read-only diagnostic — lists every Shift row for a business from today "
        "(any status), per station, with exact started_at/ended_at timestamps, so a "
        "'why is this shift's revenue showing 0 / capped oddly' question can be "
        "answered from real data instead of guessing. Never changes anything."
    )

    def add_arguments(self, parser):
        parser.add_argument('business', type=str, help='Business name (case-insensitive substring match).')
        parser.add_argument(
            '--all-time', action='store_true',
            help="Don't restrict to today — show every shift ever for this business.",
        )

    def handle(self, *args, **options):
        businesses = Business.objects.filter(name__icontains=options['business'])
        if not businesses.exists():
            self.stdout.write(self.style.ERROR(f"No business matching '{options['business']}'"))
            return

        for business in businesses:
            self.stdout.write(self.style.MIGRATE_HEADING(f'\n=== {business.name} ==='))
            qs = Shift.objects.filter(business=business).select_related('staff__userprofile').order_by('started_at')
            if not options['all_time']:
                today = timezone.localdate()
                qs = qs.filter(started_at__date=today)

            if not qs.exists():
                self.stdout.write('  No shifts found.')
                continue

            for s in qs:
                staff_name = s.staff.get_full_name() or s.staff.username if s.staff else '?'
                try:
                    role = s.staff.userprofile.role
                except Exception:
                    role = '?'
                started = timezone.localtime(s.started_at).strftime('%Y-%m-%d %H:%M:%S')
                ended = timezone.localtime(s.ended_at).strftime('%Y-%m-%d %H:%M:%S') if s.ended_at else '—'
                self.stdout.write(
                    f"  #{s.id} [{s.station or '(blank)'}] {staff_name} ({role}) "
                    f"status={s.status} auto_closed={s.auto_closed} "
                    f"started={started} ended={ended}"
                )
