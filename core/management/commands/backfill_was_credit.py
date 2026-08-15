from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "RETIRED 2026-08-15 (same day it shipped) — this command used "
        "`tab.customer_id is not null` as its signal for 'was this genuinely "
        "debt at some point', which is WRONG: an ORDINARY tab paid in full the "
        "same visit ALSO gets a Customer record auto-attached the moment it "
        "settles, completely unrelated to real debt conversion. Running this "
        "resurrected every customer's entire historical tab total as currently "
        "owed — a live incident, confirmed and undone the same day.\n\n"
        "There is no reliable way to infer 'was this genuinely debt' from "
        "current-state data alone after the fact — only at the real moment of "
        "resolution, which is what Transaction.save()'s own tab-status check "
        "already does correctly, automatically, going forward. Run "
        "revert_bad_was_credit_backfill if you already ran this command once."
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        self.stdout.write(self.style.ERROR(
            "This command is retired and does nothing. See its help text "
            "(python manage.py help backfill_was_credit) for why, and run "
            "revert_bad_was_credit_backfill instead if you already ran this once."
        ))
