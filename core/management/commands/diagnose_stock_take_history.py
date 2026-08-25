from django.core.management.base import BaseCommand

from accounts.models import Business
from core.models import Item, Shift, ShiftStockCount
from core.shift_views import _shift_station


class Command(BaseCommand):
    help = (
        "READ-ONLY. Answers 'could the same thing happen for stock takes done "
        "before your update' (2026-08-25, Roy) — for the uncounted-items "
        "visibility fix specifically: nothing was ever LOST for a past stock "
        "take, since the fix only changed what's COMPUTED and SHOWN at submit "
        "time, not what's stored. This reconstructs the exact same 'which items "
        "were shown in the modal but never got a count' answer for any PAST "
        "opening/closing stock take, by comparing that shift's real "
        "ShiftStockCount rows against the same station-scoped item list "
        "stock_take_api()'s own GET handler would have shown at the time. "
        "Changes nothing.\n\n"
        "--shift=N looks at one specific shift's opening AND closing counts. "
        "Omit it to scan every shift for the business (can be slow on an old, "
        "busy business — pass --limit to cap how many recent shifts to check)."
    )

    def add_arguments(self, parser):
        parser.add_argument('--business', type=str, required=True, help='Business name substring.')
        parser.add_argument('--shift', type=int, help='One specific Shift id.')
        parser.add_argument('--limit', type=int, default=30, help='Max recent shifts to scan (default 30).')

    def handle(self, *args, **options):
        businesses = Business.objects.filter(name__icontains=options['business'])
        if not businesses.exists():
            self.stdout.write(self.style.ERROR('No matching business.'))
            return

        for business in businesses:
            if options.get('shift'):
                shifts = Shift.objects.filter(id=options['shift'], business=business)
            else:
                shifts = (
                    Shift.objects.filter(business=business, stock_counts__isnull=False)
                    .distinct().order_by('-started_at')[:options['limit']]
                )

            if not shifts:
                self.stdout.write(f"[{business.name}] No shift with any recorded stock count found.")
                continue

            self.stdout.write(self.style.WARNING(f"\n[{business.name}]"))
            for shift in shifts:
                # Real _shift_station() (not shift.station directly) — falls
                # back to role-based inference for a pre-migration-0132 shift
                # with a blank station field, matching production exactly.
                is_kitchen = (_shift_station(shift) == 'kitchen')
                station_items = set(
                    Item.objects.filter(business=business, store__is_kitchen=is_kitchen)
                    .exclude(is_keg=True).exclude(is_produce=True)
                    .values_list('id', flat=True)
                )
                staff_name = shift.staff.get_full_name() or shift.staff.username if shift.staff else '?'

                for phase in ('opening', 'closing'):
                    counted_ids = set(
                        ShiftStockCount.objects.filter(shift=shift, phase=phase)
                        .values_list('item_id', flat=True)
                    )
                    if not counted_ids:
                        continue  # this phase was never done for this shift at all
                    uncounted_ids = station_items - counted_ids
                    when = shift.started_at.strftime('%d %b %Y %H:%M')
                    if not uncounted_ids:
                        self.stdout.write(
                            f"  shift#{shift.id} ({staff_name}, {'kitchen' if is_kitchen else 'bar'}, {when}) "
                            f"{phase}: everything was counted."
                        )
                        continue
                    names = list(
                        Item.objects.filter(id__in=uncounted_ids).order_by('description')
                        .values_list('description', flat=True)
                    )
                    self.stdout.write(self.style.ERROR(
                        f"  shift#{shift.id} ({staff_name}, {'kitchen' if is_kitchen else 'bar'}, {when}) "
                        f"{phase}: {len(names)} item(s) NEVER counted — {', '.join(names[:10])}"
                        + (f" (+{len(names) - 10} more)" if len(names) > 10 else "")
                    ))
