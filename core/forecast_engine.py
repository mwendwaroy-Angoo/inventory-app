"""
Pure-Python forecasting engine for Duka Mwecheche.
No statsmodels, no matplotlib — only stdlib + Django ORM.
Provides ETS (Holt's double exponential smoothing) and
Linear Regression models that return JSON-serialisable dicts.
"""
from datetime import date, timedelta
from collections import defaultdict


# ── helpers ────────────────────────────────────────────────────────────────

def _aggregate_daily(queryset, start: date, end: date) -> tuple[list[str], list[float]]:
    """
    Given a Transaction queryset already filtered to the desired scope,
    return two parallel lists: ISO date strings and daily revenue floats,
    filling gaps with 0.0, from start to end inclusive.
    """
    raw = defaultdict(float)
    for t in queryset.select_related("item"):
        d = t.date if isinstance(t.date, date) else t.date.date()
        price = float(t.item.selling_price or 0)
        qty   = abs(float(t.qty or 0))
        raw[d] += price * qty

    dates, values = [], []
    cur = start
    while cur <= end:
        dates.append(cur.isoformat())
        values.append(raw.get(cur, 0.0))
        cur += timedelta(days=1)
    return dates, values


def _future_dates(from_date: date, horizon: int) -> list[str]:
    return [(from_date + timedelta(days=i+1)).isoformat() for i in range(horizon)]


# ── ETS: Holt's double exponential smoothing (trend) ───────────────────────

def _holt(series: list[float], alpha=0.3, beta=0.1, steps=30) -> list[float]:
    """Holt's linear (trend) exponential smoothing — no external deps."""
    if not series:
        return [0.0] * steps
    if len(series) == 1:
        return [series[0]] * steps

    level = series[0]
    trend = series[1] - series[0]

    for y in series[1:]:
        prev_level = level
        level = alpha * y + (1 - alpha) * (level + trend)
        trend = beta  * (level - prev_level) + (1 - beta) * trend

    return [max(0.0, level + (i + 1) * trend) for i in range(steps)]


def run_ets(queryset, start: date, end: date, horizon: int) -> dict:
    hist_dates, hist_values = _aggregate_daily(queryset, start, end)
    forecast_values = _holt(hist_values, steps=horizon)
    forecast_dates  = _future_dates(end, horizon)
    return {
        "model": "ETS (Holt's Trend)",
        "hist_dates":      hist_dates,
        "hist_values":     hist_values,
        "forecast_dates":  forecast_dates,
        "forecast_values": forecast_values,
    }


# ── Linear Regression ───────────────────────────────────────────────────────

def _linreg_forecast(x: list[float], y: list[float], x_future: list[float]) -> list[float]:
    """OLS linear regression — pure Python."""
    n = len(x)
    if n < 2:
        last = y[-1] if y else 0.0
        return [max(0.0, last)] * len(x_future)

    x_mean = sum(x) / n
    y_mean = sum(y) / n
    denom  = sum((xi - x_mean) ** 2 for xi in x) or 1e-9
    slope  = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y)) / denom
    intercept = y_mean - slope * x_mean

    return [max(0.0, slope * xi + intercept) for xi in x_future]


def run_regression(queryset, start: date, end: date, horizon: int) -> dict:
    hist_dates, hist_values = _aggregate_daily(queryset, start, end)
    # Use day-index as numeric x (avoids date arithmetic in pure Python)
    x_hist   = list(range(len(hist_dates)))
    x_future = list(range(len(hist_dates), len(hist_dates) + horizon))

    forecast_values = _linreg_forecast(x_hist, hist_values, x_future)
    forecast_dates  = _future_dates(end, horizon)
    return {
        "model": "Linear Regression",
        "hist_dates":      hist_dates,
        "hist_values":     hist_values,
        "forecast_dates":  forecast_dates,
        "forecast_values": forecast_values,
    }
