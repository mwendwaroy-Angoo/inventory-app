import os
from datetime import datetime

import pandas as pd

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Run revenue forecast for businesses using Orders and/or Transactions'

    def add_arguments(self, parser):
        parser.add_argument('--source', choices=['transaction', 'order', 'both'], default='transaction', help='Revenue source')
        parser.add_argument('--cadence', choices=['daily', 'weekly', 'monthly'], default='daily', help='Aggregation cadence')
        parser.add_argument('--horizon', type=int, default=30, help='Forecast horizon (periods)')
        parser.add_argument('--business', type=int, default=None, help='Business id to filter (optional)')
        parser.add_argument('--output-dir', default='forecast/output', help='Directory to write outputs')

    def handle(self, *args, **options):
        source = options['source']
        cadence = options['cadence']
        horizon = options['horizon']
        business_id = options['business']
        output_dir = options['output_dir']

        os.makedirs(output_dir, exist_ok=True)

        # local import to avoid startup cost
        from core.models import Transaction, Order
        from forecast import forecast as fcmod

        parts = []

        if source in ('transaction', 'both'):
            qs = Transaction.objects.filter(type='Issue')
            if business_id:
                qs = qs.filter(business_id=business_id)
            rows = list(qs.values('date', 'qty', 'item__selling_price'))
            if rows:
                df_tx = pd.DataFrame(rows)
                df_tx['date'] = pd.to_datetime(df_tx['date'])
                df_tx['item__selling_price'] = pd.to_numeric(df_tx['item__selling_price'], errors='coerce').fillna(0.0)
                df_tx['revenue'] = df_tx['qty'].abs() * df_tx['item__selling_price']
                df_tx_group = df_tx.groupby(df_tx['date'].dt.floor('D')).agg({'revenue': 'sum'}).reset_index()
                parts.append(df_tx_group.rename(columns={'date': 'date'}))

        if source in ('order', 'both'):
            qs = Order.objects.filter(status__in=['paid', 'ready', 'completed'])
            if business_id:
                qs = qs.filter(business_id=business_id)
            rows = list(qs.values('created_at', 'total_amount'))
            if rows:
                df_ord = pd.DataFrame(rows)
                df_ord['date'] = pd.to_datetime(df_ord['created_at']).dt.floor('D')
                df_ord['total_amount'] = pd.to_numeric(df_ord['total_amount'], errors='coerce').fillna(0.0)
                df_ord_group = df_ord.groupby('date').agg({'total_amount': 'sum'}).reset_index()
                df_ord_group = df_ord_group.rename(columns={'total_amount': 'revenue'})
                parts.append(df_ord_group)

        if parts:
            df_hist = pd.concat(parts).groupby('date', as_index=False).agg({'revenue': 'sum'})
        else:
            df_hist = pd.DataFrame(columns=['date', 'revenue'])

        # ensure date column is datetime
        if not df_hist.empty:
            df_hist['date'] = pd.to_datetime(df_hist['date'])

        # Resample and forecast
        s = fcmod.resample_series(df_hist, cadence=cadence)
        forecast = fcmod.fit_ets_forecast(s, steps=horizon, cadence=cadence)

        # Save outputs
        biz_tag = f"biz{business_id}" if business_id else "allbiz"
        src_tag = source
        out_hist = os.path.join(output_dir, f"history_{src_tag}_{biz_tag}_{cadence}.csv")
        out_fc = os.path.join(output_dir, f"forecast_{src_tag}_{biz_tag}_{cadence}_h{horizon}.csv")
        out_plot = os.path.join(output_dir, f"forecast_{src_tag}_{biz_tag}_{cadence}_h{horizon}.png")

        df_hist.to_csv(out_hist, index=False)
        pd.DataFrame({'date': forecast.index, 'forecast': forecast.values}).to_csv(out_fc, index=False)

        # plot
        import matplotlib.pyplot as plt

        plt.figure(figsize=(10, 5))
        if not s.empty:
            plt.plot(s.index, s.values, label='history')
        plt.plot(forecast.index, forecast.values, label='forecast', linestyle='--')
        plt.legend()
        plt.title(f'Revenue forecast ({source}, {cadence}, horizon={horizon})')
        plt.xlabel('Date')
        plt.ylabel('Revenue')
        plt.tight_layout()
        plt.savefig(out_plot)
        plt.close()

        # Persist forecast to DB (if model available)
        try:
            from django.apps import apps
            Forecast = apps.get_model('core', 'Forecast')
            Business = apps.get_model('accounts', 'Business')

            # prepare JSON-friendly lists (robust to empty series or non-datetime index)
            history_list = []
            for idx, val in s.items():
                try:
                    d_iso = idx.isoformat()
                except Exception:
                    try:
                        d_iso = pd.to_datetime(idx).isoformat()
                    except Exception:
                        d_iso = str(idx)
                history_list.append({'date': d_iso, 'revenue': float(val)})

            forecast_list = []
            for idx, val in forecast.items():
                try:
                    d_iso = idx.isoformat()
                except Exception:
                    try:
                        d_iso = pd.to_datetime(idx).isoformat()
                    except Exception:
                        d_iso = str(idx)
                forecast_list.append({'date': d_iso, 'forecast': float(val)})

            biz_obj = None
            if business_id:
                biz_obj = Business.objects.filter(id=business_id).first()

            fobj = Forecast.objects.create(
                business=biz_obj,
                source=source,
                cadence=cadence,
                horizon=horizon,
                history=history_list,
                forecast=forecast_list,
                plot_path=out_plot,
                meta={'generated_by': 'manage:forecast', 'generated_at': datetime.utcnow().isoformat()},
            )
            self.stdout.write(self.style.SUCCESS(f'Persisted Forecast id={fobj.id}'))
        except Exception:
            # Non-fatal: if Forecast model/migrations not applied, skip persistence
            pass

        self.stdout.write(self.style.SUCCESS(f'Wrote history -> {out_hist}'))
        self.stdout.write(self.style.SUCCESS(f'Wrote forecast -> {out_fc}'))
        self.stdout.write(self.style.SUCCESS(f'Wrote plot -> {out_plot}'))
