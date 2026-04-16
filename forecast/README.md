# Forecasting prototype

This folder contains a minimal forecasting prototype that:

- Reads sales transactions from the Django sqlite DB (`db.sqlite3`).
- Aggregates daily/weekly/monthly revenue from `core_transaction` (type `Issue`).
- Fits a simple ETS (Holt-Winters) model and writes CSV + plot outputs.

Quick start

1. Create a Python environment and install requirements:

```bash
pip install -r forecast/requirements.txt
```

2. Run the prototype (daily cadence, 30 periods):

```bash
python "forecast/forecast.py" --db db.sqlite3 --cadence daily --horizon 30 --output-dir forecast/output_test
```

Options

- `--cadence` : `daily`, `weekly`, or `monthly`.
- `--horizon` : number of periods ahead to forecast (periods follow cadence).
- `--business` : optional business id to filter transactions for a single business.

Notes

- The script expects the default Django table names `core_transaction` and `core_item`.
- This is a prototype — for production use consider adding cross-validation, probabilistic forecasts, and richer features (promotions, holidays, price changes, etc.).

Scheduling / Precompute

- To precompute forecasts for all businesses nightly, use the provided management command:

```bash
python manage.py precompute_forecasts --source both --cadence daily --horizon 30 --output-dir forecast/output
```

- You can schedule this with system cron or a process manager. Example crontab (run nightly at 02:15):

```cron
15 2 * * * cd /path/to/project && /path/to/venv/bin/python manage.py precompute_forecasts --output-dir forecast/output >> /var/log/forecast_precompute.log 2>&1
```

- If you use Celery, you can call `call_command('forecast', ...)` from a Celery task or schedule the `precompute_forecasts` command using Celery Beat.

Celery integration

- This repository includes a minimal Celery integration: add the broker URL in the environment and start a worker + beat to run the precompute task automatically.

Environment variables (examples):

```bash
export CELERY_BROKER_URL=redis://localhost:6379/0
export CELERY_RESULT_BACKEND=redis://localhost:6379/0
```

Start a worker and beat (development):

```bash
# start worker
celery -A stockapp worker --loglevel=info

# start beat in a separate terminal
celery -A stockapp beat --loglevel=info
```

The project adds a `precompute_forecasts_task` Celery task (in `core.tasks`) and an optional default beat schedule (nightly 02:15) configured in `stockapp/settings.py` when Celery is installed. You can manage recurring schedules via the Django admin (django-celery-beat) as well.
