import argparse
import os
import sqlite3
from datetime import datetime

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def get_revenue_df(db_path='db.sqlite3', business_id=None, start_date=None, end_date=None):
    """Read transactions from a Django sqlite DB and aggregate daily revenue.

    Expects Django tables `core_transaction` and `core_item` (default names).
    Returns a DataFrame with columns `date` (datetime) and `revenue` (float).
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"DB not found: {db_path}")

    conn = sqlite3.connect(db_path)
    params = []
    q = (
        "SELECT t.date as date, "
        "SUM(ABS(t.qty) * COALESCE(i.selling_price, 0)) AS revenue "
        "FROM core_transaction t "
        "JOIN core_item i ON t.item_id = i.id "
        "WHERE t.type = 'Issue' "
    )
    if business_id is not None:
        q += "AND t.business_id = ? "
        params.append(business_id)
    if start_date is not None and end_date is not None:
        q += "AND date BETWEEN ? AND ? "
        params.extend([start_date, end_date])

    q += "GROUP BY date ORDER BY date"

    df = pd.read_sql_query(q, conn, params=params, parse_dates=['date'])
    conn.close()

    if 'revenue' not in df.columns:
        df['revenue'] = 0.0

    df['revenue'] = pd.to_numeric(df['revenue'], errors='coerce').fillna(0.0)
    df = df.sort_values('date')
    return df


def resample_series(df, cadence='daily'):
    """Resample the revenue series to the requested cadence.

    cadence: 'daily', 'weekly', 'monthly'
    Returns a Series indexed by period start and named 'revenue'.
    """
    if df.empty:
        return pd.Series(dtype=float)

    freq_map = {'daily': 'D', 'weekly': 'W-MON', 'monthly': 'M'}
    rule = freq_map.get(cadence, cadence)

    s = df.set_index('date')['revenue'].resample(rule).sum()
    # ensure full range
    s = s.asfreq(rule, fill_value=0.0)
    return s


def fit_ets_forecast(series, steps=30, cadence='daily'):
    """Fit a simple Holt-Winters ETS model and forecast `steps` ahead.
    Falls back to a naive forecast when the model can't be fit.
    """
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
    except Exception as e:
        raise RuntimeError("statsmodels is required for ETS forecasting") from e

    if series.empty:
        return pd.Series([0.0] * steps, index=pd.date_range(start=pd.Timestamp.today(), periods=steps, freq='D'))

    # Determine seasonal periods heuristically
    if cadence == 'daily':
        seasonal_periods = 7
    elif cadence == 'weekly':
        seasonal_periods = 52
    elif cadence == 'monthly':
        seasonal_periods = 12
    else:
        seasonal_periods = None

    try:
        if seasonal_periods and len(series) >= 2 * seasonal_periods:
            model = ExponentialSmoothing(series, trend='add', seasonal='add', seasonal_periods=seasonal_periods)
        else:
            model = ExponentialSmoothing(series, trend='add', seasonal=None)
        fitted = model.fit(optimized=True)
        forecast = fitted.forecast(steps)
    except Exception:
        # naive fallback: repeat last observed value
        last = float(series.dropna().iloc[-1]) if len(series.dropna()) else 0.0
        start = series.index[-1] if len(series.index) else pd.Timestamp.today()
        freq = series.index.freq or pd.infer_freq(series.index) or 'D'
        idx = pd.date_range(start=start + pd.tseries.frequencies.to_offset(freq), periods=steps, freq=freq)
        forecast = pd.Series([last] * steps, index=idx)

    return forecast


def run_from_db(db_path='db.sqlite3', cadence='daily', horizon=30, business_id=None, output_dir='forecast/output'):
    os.makedirs(output_dir, exist_ok=True)
    df = get_revenue_df(db_path=db_path, business_id=business_id)
    s = resample_series(df, cadence=cadence)
    # choose steps based on horizon interpreted as number of periods
    steps = int(horizon)
    forecast = fit_ets_forecast(s, steps=steps, cadence=cadence)

    # Save data
    ts_out = pd.DataFrame({'date': s.index, 'revenue': s.values})
    ts_out.to_csv(os.path.join(output_dir, 'history.csv'), index=False)
    fc_out = pd.DataFrame({'date': forecast.index, 'forecast': forecast.values})
    fc_out.to_csv(os.path.join(output_dir, 'forecast.csv'), index=False)

    # plot
    plt.figure(figsize=(10, 5))
    plt.plot(s.index, s.values, label='history')
    plt.plot(forecast.index, forecast.values, label='forecast', linestyle='--')
    plt.legend()
    plt.title(f'Revenue forecast ({cadence}, horizon={horizon})')
    plt.xlabel('Date')
    plt.ylabel('Revenue')
    plt.tight_layout()
    plot_path = os.path.join(output_dir, 'forecast.png')
    plt.savefig(plot_path)
    plt.close()

    print(f"Saved history -> {os.path.join(output_dir, 'history.csv')}")
    print(f"Saved forecast -> {os.path.join(output_dir, 'forecast.csv')}")
    print(f"Saved plot -> {plot_path}")
    return ts_out, fc_out


def main():
    p = argparse.ArgumentParser(description='Simple revenue forecasting prototype')
    p.add_argument('--db', default='db.sqlite3', help='Path to sqlite DB')
    p.add_argument('--cadence', default='daily', choices=['daily', 'weekly', 'monthly'], help='Aggregation cadence')
    p.add_argument('--horizon', type=int, default=30, help='Forecast horizon (number of periods)')
    p.add_argument('--business', type=int, default=None, help='Business id to filter (optional)')
    p.add_argument('--output-dir', default='forecast/output', help='Directory to write outputs')
    args = p.parse_args()

    run_from_db(db_path=args.db, cadence=args.cadence, horizon=args.horizon, business_id=args.business, output_dir=args.output_dir)


if __name__ == '__main__':
    main()
