from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Business


class Command(BaseCommand):
    help = (
        "Read-only diagnostic — lists every individual transaction that "
        "till_expected_cash() (the 'Kiasi Kinachotarajiwa Kwenye Counter Sasa' "
        "dashboard tile) summed for one business+station since its anchor, so a "
        "'this number doesn't match what's physically in the till' report can be "
        "checked against real rows instead of guessed at. Never changes anything."
    )

    def add_arguments(self, parser):
        parser.add_argument('business', type=str, help='Business name (case-insensitive substring match).')
        parser.add_argument('station', choices=['bar', 'kitchen'])

    def handle(self, *args, **options):
        from core.shift_views import till_expected_cash
        from core.models import Transaction, PettyCash, CustomerDebtPayment, Shift

        businesses = Business.objects.filter(name__icontains=options['business'])
        if not businesses.exists():
            self.stdout.write(self.style.ERROR(f"No business matching '{options['business']}'"))
            return
        business = businesses.first()
        station = options['station']
        is_kitchen = (station == 'kitchen')

        result = till_expected_cash(business, station)
        self.stdout.write(self.style.MIGRATE_HEADING(f'\n=== {business.name} — {station} ==='))
        if not result['anchor_established']:
            self.stdout.write('No anchor established yet for this station.')
            return

        self.stdout.write(f"Anchor: {result['anchor_label']}")
        self.stdout.write(f"Base: KES {result['breakdown']['base']:,.2f}")
        window_start = result['anchor_at']
        now = timezone.now()

        self.stdout.write(self.style.MIGRATE_HEADING('\n-- Cash sales since anchor --'))
        txns = Transaction.objects.filter(
            business=business, type='Issue', payment_method='cash',
            item__store__is_kitchen=is_kitchen, created_at__gt=window_start, created_at__lte=now,
        ).exclude(invoice_no='[SVQ]').select_related('item', 'recorded_by').order_by('created_at')
        total = 0.0
        for t in txns:
            amt = float(t.revenue())
            total += amt
            recorder = (t.recorded_by.get_full_name() or t.recorded_by.username) if t.recorded_by else '?'
            when = timezone.localtime(t.created_at).strftime('%Y-%m-%d %H:%M:%S')
            self.stdout.write(f"  #{t.id} {when} KES {amt:,.2f} — {t.item.description if t.item_id else '?'} — by {recorder}")
        self.stdout.write(f"  TOTAL: KES {total:,.2f} (n={txns.count()})")

        self.stdout.write(self.style.MIGRATE_HEADING('\n-- Debt recovered (cash) since anchor --'))
        debts = CustomerDebtPayment.objects.filter(
            business=business, payment_method='cash', source=station,
            paid_at__gt=window_start, paid_at__lte=now,
        ).select_related('customer', 'recorded_by').order_by('paid_at')
        dtotal = 0.0
        for d in debts:
            dtotal += float(d.amount_paid)
            recorder = (d.recorded_by.get_full_name() or d.recorded_by.username) if d.recorded_by else '?'
            when = timezone.localtime(d.paid_at).strftime('%Y-%m-%d %H:%M:%S')
            self.stdout.write(f"  #{d.id} {when} KES {d.amount_paid} — {d.customer.name} — by {recorder}")
        self.stdout.write(f"  TOTAL: KES {dtotal:,.2f} (n={debts.count()})")

        self.stdout.write(self.style.MIGRATE_HEADING('\n-- Approved petty cash since anchor --'))
        pettys = PettyCash.objects.filter(
            business=business, status='approved', station=station,
            created_at__gt=window_start, created_at__lte=now,
        ).select_related('recorded_by').order_by('created_at')
        ptotal = 0.0
        for p in pettys:
            ptotal += float(p.amount)
            recorder = (p.recorded_by.get_full_name() or p.recorded_by.username) if p.recorded_by else '?'
            when = timezone.localtime(p.created_at).strftime('%Y-%m-%d %H:%M:%S')
            self.stdout.write(f"  #{p.id} {when} KES {p.amount} — {p.reason or '(no reason)'} — by {recorder}")
        self.stdout.write(f"  TOTAL: KES {ptotal:,.2f} (n={pettys.count()})")

        self.stdout.write(self.style.MIGRATE_HEADING('\n-- Shifts opened since anchor (banked_amount) --'))
        from core.shift_views import _station_q
        shifts = Shift.objects.filter(
            business=business, started_at__gt=window_start, started_at__lte=now,
        ).exclude(staff__userprofile__role='owner').filter(_station_q(is_kitchen)).select_related('staff')
        btotal = 0.0
        for s in shifts:
            banked = float(s.banked_amount or 0)
            btotal += banked
            staff_name = s.staff.get_full_name() or s.staff.username
            self.stdout.write(f"  #{s.id} {staff_name} banked=KES {banked:,.2f}")
        self.stdout.write(f"  TOTAL banked: KES {btotal:,.2f}")

        self.stdout.write(self.style.SUCCESS(
            f"\nFormula: {result['breakdown']['base']:,.2f} + {result['breakdown']['cash_sales']:,.2f} "
            f"+ {result['breakdown']['debt_recovered']:,.2f} - {result['breakdown']['petty_cash']:,.2f} "
            f"- {result['breakdown']['banked']:,.2f} = {result['expected_cash']:,.2f}"
        ))
