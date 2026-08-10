from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Shift, Transaction


class Command(BaseCommand):
    help = (
        "One-time backfill: Shift.station was added 2026-07-27/28 to replace the "
        "old role-based bar-vs-kitchen inference (_shift_station()'s fallback), "
        "which is wrong for any manager or cross-access staffer working the "
        "counter that isn't their nominal role — the exact bug that produced a "
        "misattributed till figure at Monsoon Inn. Every Shift closed BEFORE "
        "that migration still has a blank station and still falls back to the "
        "same buggy role-based guess today. This command infers the real "
        "station for each blank-station shift from that staffer's OWN "
        "Transactions actually recorded during the shift's own time window "
        "(item.store.is_kitchen) — a real activity signal, not a role guess — "
        "and only writes a correction when the signal is unambiguous (one "
        "station has zero transactions, or one station has at least 80% of "
        "them). Anything murkier (genuinely mixed activity — e.g. a concurrent "
        "bar+kitchen shift pair for the same cross-access staffer) is left "
        "alone and reported separately for manual review, never guessed. "
        "Safe to re-run — only ever touches rows with a currently-blank "
        "station. Run --dry-run first and read the output before running for "
        "real."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would change without saving anything.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        qs = (
            Shift.objects.filter(station='')
            .select_related('staff', 'staff__userprofile', 'business')
            .order_by('business_id', 'started_at')
        )

        corrected = 0       # activity-based answer differs from the old role guess
        confirmed = 0       # activity-based answer agrees with the old role guess (stamped anyway)
        no_signal = 0        # zero transactions found in the window — left blank
        ambiguous = 0         # real activity on both sides, no clear majority — left blank

        for shift in qs:
            window_end = shift.ended_at or timezone.now()
            txns = (
                Transaction.objects.filter(
                    business=shift.business,
                    recorded_by=shift.staff,
                    created_at__gte=shift.started_at,
                    created_at__lte=window_end,
                )
                .select_related('item__store')
            )
            kitchen_count = 0
            bar_count = 0
            for t in txns:
                try:
                    if t.item.store.is_kitchen:
                        kitchen_count += 1
                    else:
                        bar_count += 1
                except Exception:
                    continue

            total = kitchen_count + bar_count
            if total == 0:
                no_signal += 1
                continue

            if kitchen_count == 0:
                inferred = 'bar'
            elif bar_count == 0:
                inferred = 'kitchen'
            else:
                dominant_ratio = max(kitchen_count, bar_count) / total
                if dominant_ratio < 0.8:
                    ambiguous += 1
                    self.stdout.write(self.style.WARNING(
                        f"  AMBIGUOUS [{shift.business.name}] Shift #{shift.id} "
                        f"staff={shift.staff.username} kitchen_txns={kitchen_count} "
                        f"bar_txns={bar_count} — left blank, review manually."
                    ))
                    continue
                inferred = 'kitchen' if kitchen_count > bar_count else 'bar'

            try:
                role_based = 'kitchen' if shift.staff.userprofile.role == 'kitchen' else 'bar'
            except Exception:
                role_based = 'bar'

            if inferred != role_based:
                corrected += 1
                self.stdout.write(self.style.SUCCESS(
                    f"  FIX [{shift.business.name}] Shift #{shift.id} "
                    f"staff={shift.staff.username} — role guessed '{role_based}', "
                    f"actual activity says '{inferred}' "
                    f"(kitchen_txns={kitchen_count}, bar_txns={bar_count})"
                ))
            else:
                confirmed += 1

            if not dry_run:
                shift.station = inferred
                shift.save(update_fields=['station'])

        label = "DRY RUN — " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(
            f"\n{label}{corrected} shift(s) had their station corrected, "
            f"{confirmed} confirmed the existing role-based guess was already right, "
            f"{no_signal} had no transaction activity to infer from, "
            f"{ambiguous} had genuinely mixed activity and were left for manual review."
        ))
