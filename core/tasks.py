import io
import traceback
from django.utils import timezone
from django.core.management import call_command

from .models import ImportJob


# Celery-friendly shared_task decorator: use Celery when available, otherwise
# provide a no-op decorator so imports don't fail when Celery isn't installed.
try:
    from celery import shared_task
except Exception:
    def shared_task(*a, **k):
        def _dec(fn):
            return fn
        return _dec


@shared_task(bind=True)
def precompute_forecasts_task(self, source='both', cadence='daily', horizon=30, output_dir='forecast/output'):
    """Celery task wrapper that runs the `precompute_forecasts` management command.

    Arguments mirror the management command and are left as simple types
    so they serialize cleanly via Celery.
    """
    try:
        call_command('precompute_forecasts', '--source', source, '--cadence', cadence, '--horizon', str(horizon), '--output-dir', output_dir)
    except Exception:
        # Let Celery record the failure/traceback; don't re-raise here to allow retries configuration.
        raise


def run_import_job(job_id):
    """Background runner for queued import jobs.

    This updates the ImportJob status and captures stdout/stderr into the
    job.result_text field.
    """
    try:
        job = ImportJob.objects.get(id=job_id)
    except ImportJob.DoesNotExist:
        return

    job.status = 'running'
    job.started_at = timezone.now()
    job.save(update_fields=['status', 'started_at'])

    out = io.StringIO()
    try:
        if job.job_type == 'products':
            kwargs = {}
            if job.commit:
                kwargs['commit'] = True
            if job.store:
                kwargs['store_id'] = job.store.id
            call_command('import_products', job.file_path, stdout=out, **kwargs)
        else:
            kwargs = {}
            if job.commit:
                kwargs['commit'] = True
            call_command('import_taxonomy', job.file_path, stdout=out, **kwargs)

        job.result_text = out.getvalue()
        job.status = 'completed'
    except Exception as e:
        tb = traceback.format_exc()
        job.result_text = f"Exception: {e}\n\n{tb}\n\nPartial output:\n" + out.getvalue()
        job.status = 'failed'
    finally:
        job.finished_at = timezone.now()
        job.save(update_fields=['result_text', 'status', 'finished_at'])


@shared_task
def forecast_async_task(forecast_obj_id, business_id, source='both', cadence='daily', horizon=30, date_from=None, date_to=None, product_id=None):
    """Background worker to compute a forecast and update the Forecast DB record.

    This supports being called via Celery (.delay) or directly when Celery is not available.
    """
    from django.apps import apps
    import pandas as pd
    import traceback
    try:
        Forecast = apps.get_model('core', 'Forecast')
    except Exception:
        Forecast = None

    fobj = None
    if forecast_obj_id and Forecast:
        try:
            fobj = Forecast.objects.get(id=forecast_obj_id)
        except Exception:
            fobj = None

    # Mark as running if we have a record
    if fobj:
        meta = fobj.meta or {}
        meta.update({'status': 'running'})
        fobj.meta = meta
        try:
            fobj.save(update_fields=['meta'])
        except Exception:
            pass

    try:
        from .models import Transaction, Order
        parts = []

        if source in ('transaction', 'both'):
            tx_qs = Transaction.objects.filter(type='Issue', business_id=business_id)
            if date_from:
                tx_qs = tx_qs.filter(date__gte=date_from)
            if date_to:
                tx_qs = tx_qs.filter(date__lte=date_to)
            if product_id:
                try:
                    tx_qs = tx_qs.filter(item_id=int(product_id))
                except Exception:
                    pass
            rows = list(tx_qs.values('date', 'qty', 'item__selling_price'))
            if rows:
                df_tx = pd.DataFrame(rows)
                df_tx['date'] = pd.to_datetime(df_tx['date'])
                df_tx['item__selling_price'] = pd.to_numeric(df_tx['item__selling_price'], errors='coerce').fillna(0.0)
                df_tx['revenue'] = df_tx['qty'].abs() * df_tx['item__selling_price']
                parts.append(df_tx.groupby(df_tx['date'].dt.floor('D')).agg({'revenue': 'sum'}).reset_index())

        if source in ('order', 'both'):
            ord_qs = Order.objects.filter(business_id=business_id, status__in=['paid', 'ready', 'completed'])
            if date_from:
                ord_qs = ord_qs.filter(created_at__date__gte=date_from)
            if date_to:
                ord_qs = ord_qs.filter(created_at__date__lte=date_to)
            rows = list(ord_qs.values('created_at', 'total_amount'))
            if rows:
                df_ord = pd.DataFrame(rows)
                df_ord['date'] = pd.to_datetime(df_ord['created_at']).dt.floor('D')
                df_ord['total_amount'] = pd.to_numeric(df_ord['total_amount'], errors='coerce').fillna(0.0)
                parts.append(df_ord.groupby('date').agg({'total_amount': 'sum'}).reset_index().rename(columns={'total_amount': 'revenue'}))

        if parts:
            df_hist = pd.concat(parts).groupby('date', as_index=False).agg({'revenue': 'sum'})
        else:
            df_hist = pd.DataFrame(columns=['date', 'revenue'])

        if not df_hist.empty:
            df_hist['date'] = pd.to_datetime(df_hist['date'])

        from forecast import forecast as fcmod
        s = fcmod.resample_series(df_hist, cadence=cadence)
        forecast_series = fcmod.fit_ets_forecast(s, steps=horizon, cadence=cadence)

        history_list = [{'date': d.isoformat(), 'revenue': float(v)} for d, v in zip(s.index.to_pydatetime(), s.values.tolist())]
        forecast_list = [{'date': d.isoformat(), 'forecast': float(v)} for d, v in zip(forecast_series.index.to_pydatetime(), forecast_series.values.tolist())]

        if fobj:
            fobj.history = history_list
            fobj.forecast = forecast_list
            meta = fobj.meta or {}
            meta.update({'status': 'completed', 'generated_by': 'async_task'})
            fobj.meta = meta
            try:
                fobj.generated_at = timezone.now()
                fobj.save(update_fields=['history', 'forecast', 'meta', 'generated_at'])
            except Exception:
                try:
                    fobj.save()
                except Exception:
                    pass
        return True
    except Exception as exc:
        tb = traceback.format_exc()
        if fobj:
            meta = fobj.meta or {}
            meta.update({'status': 'failed', 'error': str(exc), 'traceback': tb})
            fobj.meta = meta
            try:
                fobj.save(update_fields=['meta'])
            except Exception:
                pass
        raise
