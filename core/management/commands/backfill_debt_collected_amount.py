from django.db.models import Q

from django.core.management.base import BaseCommand

from core.models import BarTabEntry


class Command(BaseCommand):
    help = (
        "One-time companion to migration 0175 (BarTabEntry.debt_collected_amount, "
        "2026-08-24). That field isolates the portion of an entry ever collected "
        "via the DEBT TRACKER specifically, as opposed to the broader amount_paid "
        "(which since the same day also grows from an ordinary counter settle). "
        "It defaults to 0, so every row that existed before the migration has it "
        "understated.\n\n"
        "Why that matters, and why this is safe to derive: verified by reading the "
        "pre-2026-08-24 source directly (git show 94aaba4) — the ONLY writer of "
        "BarTabEntry.amount_paid before that date was debt_views."
        "_reconcile_tab_entries_for_debt_payment (its fully-covered branch stamping "
        "amount_paid=F('amount'), and its partial branch incrementing it). "
        "mark_fully_paid() and split_paid_unpaid_locked()'s own amount_paid write "
        "both landed the same day as the new field. So every non-zero amount_paid "
        "in pre-existing data came from the debt tracker, and "
        "debt_collected_amount should equal it.\n\n"
        "Two things depend on getting this right:\n"
        "  * revoke_payment_locked() rolls amount_paid back to "
        "    debt_collected_amount. Left at 0 on a legacy entry the debt tracker "
        "    genuinely paid off, revoking would reset amount_paid to 0 and "
        "    re-inflate the customer's debt by money they already paid.\n"
        "  * _reconcile()/till_expected_cash() subtract debt_collected_amount from "
        "    a transaction's sale_amount before counting it as fresh cash/mpesa. "
        "    Left at 0, an entry that was debt-collected and later flips to cash/"
        "    mpesa gets double-counted against debt_recovered_*.\n\n"
        "Scoped with the SAME discriminator _rebuild_tab_entry_state_for_customer "
        "already uses in production — the transaction must still be "
        "payment_method='credit' OR carry was_credit=True. That cleanly separates "
        "debt-tracker-collected entries (a debt settle always happens on a "
        "non-OPEN tab, so the credit→cash/mpesa transition stamps was_credit; a "
        "partial one leaves it on 'credit') from an ordinary counter settle (which "
        "happens while the tab is still OPEN, so was_credit is never stamped). "
        "That makes this correct whenever it runs, not only immediately after "
        "deploy. Safe to re-run — only ever touches rows still sitting at 0."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='List what would be backfilled without saving anything.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        qs = (
            BarTabEntry.objects
            .filter(amount_paid__gt=0, debt_collected_amount=0)
            .filter(
                Q(transaction__payment_method='credit') | Q(transaction__was_credit=True),
                transaction__type='Issue',
            )
            .select_related('transaction', 'tab__business')
        )

        count = 0
        total = 0
        for entry in qs:
            biz_name = entry.tab.business.name if entry.tab_id else '?'
            self.stdout.write(
                f"  [{biz_name}] tab #{entry.tab_id} — {entry.description}: "
                f"amount={entry.amount} amount_paid={entry.amount_paid} "
                f"is_paid={entry.is_paid} → debt_collected_amount 0 -> {entry.amount_paid}"
            )
            if not dry_run:
                entry.debt_collected_amount = entry.amount_paid
                entry.save(update_fields=['debt_collected_amount'])
            count += 1
            total += float(entry.amount_paid)

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"DRY RUN — would backfill {count} entr(ies), KES {total:,.2f} total."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Backfilled {count} entr(ies), KES {total:,.2f} total."
            ))
