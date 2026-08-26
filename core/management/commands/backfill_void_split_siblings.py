from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction as db_txn

from accounts.models import Business
from core.keg_views import _reverse_stock_movement_envelope
from core.models import Transaction


class Command(BaseCommand):
    help = (
        "One-time repair for the pre-2026-08-26 gap in void_direct_transaction() "
        "(🗑 Futa, from the 'Recent Sales'/Malipo ya Hivi Karibuni panel): voiding "
        "a split-payment (Gawanya/Deni) ROOT transaction alone restored the sale's "
        "WHOLE physical qty to stock, while any still-LIVE sibling (the split-off "
        "cash/mpesa/credit portion, created by Transaction.split_payment_method_"
        "locked) kept its own revenue recognized for that exact same now-'never "
        "sold' item — a self-contradictory ledger: stock says the item never left "
        "the shelf, a sibling transaction says it was paid for.\n\n"
        "Finds every VOID direct-sale (type=Issue, no tab_entry) Transaction and "
        "walks its downward split_from closure for any still-live descendant. A "
        "live cash/mpesa descendant is voided here exactly the way the fixed live "
        "code path now does (qty→0 — always already 0 for a split sibling by "
        "construction, so this never further restores stock; the source revenue-"
        "envelope, if any, is reversed by its own share; payment_method→'void'). "
        "A live CREDIT descendant is NEVER auto-voided — it needs the debt "
        "tracker's own correction tool first (the customer's debt must be cleared "
        "through 'Ilikuwa Kosa', not silently erased by a backfill script) — it is "
        "only ever reported here for manual follow-up.\n\n"
        "--dry-run previews every finding and change with zero writes. Safe to "
        "re-run — only ever acts on a live descendant still hanging off an "
        "already-void ancestor; nothing here is re-processed once fixed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--business', type=str, default=None,
            help='Restrict to one business by exact name (default: every business).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would change without saving anything.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        biz_name = options['business']

        businesses = Business.objects.all().order_by('name')
        if biz_name:
            businesses = businesses.filter(name=biz_name)
            if not businesses.exists():
                self.stderr.write(self.style.ERROR(f'No business named "{biz_name}".'))
                return

        total_groups = 0
        total_voided = 0
        total_voided_kes = 0.0
        total_credit_flagged = 0

        for biz in businesses:
            void_roots = (
                Transaction.objects.filter(
                    business=biz, type='Issue', tab_entry__isnull=True, payment_method='void',
                )
                .select_related('item')
                .order_by('id')
            )

            already_seen = set()
            for root_void in void_roots:
                if root_void.id in already_seen:
                    continue
                already_seen.add(root_void.id)

                # Downward split_from closure from this void transaction —
                # mirrors void_direct_transaction()'s own live-fix walk.
                group_ids = {root_void.id}
                frontier = [root_void.id]
                live_descendants = []
                credit_descendants = []
                while frontier:
                    kids = list(
                        Transaction.objects.select_related('item')
                        .filter(business=biz, split_from_id__in=frontier)
                        .exclude(id__in=group_ids)
                    )
                    if not kids:
                        break
                    for k in kids:
                        group_ids.add(k.id)
                        already_seen.add(k.id)
                        if k.payment_method == 'void':
                            continue
                        elif k.payment_method == 'credit':
                            credit_descendants.append(k)
                        else:
                            live_descendants.append(k)
                    frontier = [k.id for k in kids]

                if not live_descendants and not credit_descendants:
                    continue

                total_groups += 1
                item_desc = root_void.item.description if root_void.item_id else '(no item)'
                self.stdout.write(
                    f'[{biz.name}] void txn #{root_void.id} ("{item_desc}") has '
                    f'{len(live_descendants)} live cash/mpesa descendant(s) and '
                    f'{len(credit_descendants)} credit descendant(s) still standing.'
                )

                for c in credit_descendants:
                    total_credit_flagged += 1
                    self.stdout.write(self.style.WARNING(
                        f'    ⚠ txn #{c.id} is CREDIT (KES {float(c.revenue()):,.2f}, '
                        f'recipient="{c.recipient}") — NOT auto-voided. Resolve manually via '
                        f'the debt tracker\'s "Ilikuwa Kosa" write-off tool.'
                    ))

                for m in live_descendants:
                    old_amount = float(m.revenue())
                    self.stdout.write(
                        f'    → txn #{m.id} ({m.payment_method}, KES {old_amount:,.2f}) — '
                        + ('would void' if dry_run else 'voiding')
                    )
                    total_voided += 1
                    total_voided_kes += old_amount
                    if dry_run:
                        continue
                    with db_txn.atomic():
                        locked = Transaction.objects.select_for_update().get(id=m.id)
                        if locked.payment_method == 'void':
                            continue
                        _reverse_stock_movement_envelope(locked)
                        locked.qty = Decimal('0')
                        locked.payment_method = 'void'
                        tag = '[FUTWA: backfill — sehemu ya mauzo yaliyofutwa awali]'
                        locked.recipient = (f'{locked.recipient} {tag}' if locked.recipient else tag)[:200]
                        locked.save(update_fields=['qty', 'payment_method', 'recipient'])

        prefix = 'DRY RUN — ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}Split groups affected: {total_groups}. '
            f'{"Would void" if dry_run else "Voided"}: {total_voided} '
            f'(KES {total_voided_kes:,.2f} removed from revenue). '
            f'Flagged for manual review (credit): {total_credit_flagged}.'
        ))
