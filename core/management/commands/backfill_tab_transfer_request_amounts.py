from django.core.management.base import BaseCommand

from core.models import TabTransferRequest


class Command(BaseCommand):
    help = (
        "Repair for a 2026-08-23 bug (see BarTabEntry.remaining_amount()'s own "
        "docstring), live report from Roy on 2026-08-24: an already-partially-"
        "paid tab entry (e.g. an 80 KES cup with 30 already paid via the debt "
        "tracker) proposed as a full-item transfer to a DIFFERENT customer's "
        "debt quoted the WHOLE original price (80) instead of the true "
        "remaining balance (50) — because split_and_transfer_locked()'s "
        "whole-item branch and propose_whole_tab_locked() both used "
        "entry.amount directly, with zero awareness of entry.amount_paid, "
        "until the same-day fix added BarTabEntry.remaining_amount() and "
        "wired both call sites through it.\n\n"
        "The actual money owed was never wrong — _get_customer_debt_data() "
        "always recomputes 'remaining' live from entry.amount_paid, "
        "completely independent of this field — but TabTransferRequest.amount "
        "itself is a stored SNAPSHOT read directly by: the SMS sent when the "
        "transfer is proposed (already sent, can't be undone), and the live "
        "pending-transfer banner on the destination customer's own receipt/"
        "tab-live page and every tabs drawer (still wrong for as long as the "
        "row stays PENDING, or if anything is ever built to show resolved-"
        "transfer history). This command corrects the STORED field only —\n\n"
        "Only ever touches rows created via a FULL-item transfer (paid_amount "
        "== 0 — both the single-item and whole-tab paths always set this; a "
        "REAL partial split, source_kept_paid, always has a real paid_amount "
        "and was never affected, since its own remainder entry.amount was "
        "already correctly the true remainder at creation time) whose stored "
        "amount no longer matches entry.remaining_amount() computed NOW. "
        "Since entry.amount never changes on this path, a mismatch can only "
        "mean entry.amount_paid grew after the request's amount was stamped — "
        "exactly the fingerprint of this bug. Recomputing from CURRENT "
        "amount_paid is a deliberate best-effort choice: it can't perfectly "
        "reconstruct what amount_paid was at the exact moment of proposal if "
        "further payments landed on the entry since (either before response, "
        "or after acceptance by its new owner) — but always moves the stored "
        "value TOWARD what's genuinely still owed right now, never away from "
        "it, and is exactly what a stale display should show going forward. "
        "Never touches BarTabEntry, Transaction, or any real balance — a "
        "pure correction of one display/audit field. Safe to re-run."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='List what would be fixed without saving anything.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        qs = (
            TabTransferRequest.objects.filter(paid_amount=0)
            .select_related('entry', 'source_tab', 'dest_tab', 'source_tab__business')
        )

        count = 0
        for tfr in qs:
            entry = tfr.entry
            correct = entry.remaining_amount()
            if tfr.amount == correct:
                continue
            biz_name = tfr.source_tab.business.name if tfr.source_tab_id else '?'
            self.stdout.write(
                f"  [{biz_name}] transfer #{tfr.id} ({tfr.status}) "
                f"{tfr.source_tab.customer_name} -> {tfr.dest_tab.customer_name} "
                f"({entry.description}): stored KES {tfr.amount} -> KES {correct} "
                f"(entry.amount={entry.amount}, amount_paid={entry.amount_paid})"
            )
            if not dry_run:
                tfr.amount = correct
                tfr.save(update_fields=['amount'])
            count += 1

        if dry_run:
            self.stdout.write(self.style.WARNING(f"DRY RUN — would fix {count} transfer request(s)."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Fixed {count} transfer request(s)."))
