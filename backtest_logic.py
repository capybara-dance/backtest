from datetime import date

import numpy as np
import pandas as pd
import yfinance as yf


def parse_tickers(raw_input):
    """Parse comma-separated tickers and deduplicate while preserving order."""
    return list(dict.fromkeys(
        t for t in (s.strip().upper() for s in raw_input.split(",")) if t
    ))


def fetch_ticker_data(tickers, fetch_start, fetch_end):
    """Fetch OHLCV history and metadata for each symbol."""
    ticker_data = {}
    failed = []

    for symbol in tickers:
        try:
            ticker_obj = yf.Ticker(symbol)
            df = ticker_obj.history(start=fetch_start, end=fetch_end)
            if df.empty:
                failed.append((symbol, "데이터 없음"))
                continue

            if isinstance(df.index, pd.DatetimeIndex):
                if df.index.tz is not None:
                    df.index = df.index.tz_localize(None)
                df.index = df.index.normalize()

            try:
                info = ticker_obj.info
            except Exception:
                info = {}

            ticker_data[symbol] = {"df": df, "info": info}
        except (ConnectionError, TimeoutError, OSError):
            failed.append((symbol, "네트워크 연결 오류"))
        except Exception:
            failed.append((symbol, "오류 발생"))

    return ticker_data, failed


def _safe_date(ts):
    return ts.date() if not pd.isnull(ts) else None


def compute_date_bounds(ticker_data):
    """Return (start_default, start_min, end_max, end_default) for date input widgets."""
    data_start_default = max(
        _safe_date(data["df"].index[0]) for data in ticker_data.values()
        if not data["df"].empty and _safe_date(data["df"].index[0]) is not None
    )
    data_start_min = min(
        _safe_date(data["df"].index[0]) for data in ticker_data.values()
        if not data["df"].empty and _safe_date(data["df"].index[0]) is not None
    )
    data_end_max = max(
        _safe_date(data["df"].index[-1]) for data in ticker_data.values()
        if not data["df"].empty and _safe_date(data["df"].index[-1]) is not None
    )
    data_end_default = min(date.today(), data_end_max)
    return data_start_default, data_start_min, data_end_max, data_end_default


def normalize_weights(raw_weights, symbols):
    total_w = sum(raw_weights.values())
    if total_w > 0:
        weights = {s: raw_weights.get(s, 0.0) / total_w for s in symbols}
    else:
        n = len(symbols)
        weights = {s: 1 / n for s in symbols}
    return weights, total_w


def get_rebalance_dates(index, freq):
    idx = pd.DatetimeIndex(index)
    freq_map = {"월별": "MS", "분기별": "QS", "연간": "YS"}
    rebal = pd.Series(idx, index=idx).resample(freq_map[freq]).first()
    return set(rebal.dropna())


def get_monthly_contribution_dates(index):
    idx = pd.DatetimeIndex(index)
    contrib = pd.Series(idx, index=idx).resample("MS").first()
    return set(contrib.dropna())


def run_lump_sum(price_df, initial_amount, weights, rebalance, rebalance_freq):
    symbols = list(price_df.columns)
    w = {s: weights.get(s, 0.0) for s in symbols}

    first_prices = price_df.iloc[0]
    shares = {
        s: initial_amount * w[s] / first_prices[s] if first_prices[s] > 0 else 0.0
        for s in symbols
    }

    rb_dates = get_rebalance_dates(price_df.index, rebalance_freq) if rebalance else set()

    values = []
    for i, (dt, prices) in enumerate(price_df.iterrows()):
        if rebalance and dt in rb_dates and i > 0:
            total_val = sum(shares[s] * prices[s] for s in symbols)
            if total_val > 0:
                shares = {
                    s: total_val * w[s] / prices[s] if prices[s] > 0 else shares[s]
                    for s in symbols
                }
        values.append(sum(shares[s] * prices[s] for s in symbols))

    portfolio = pd.Series(values, index=price_df.index)
    invested = pd.Series(float(initial_amount), index=price_df.index)
    return portfolio, invested


def run_dca(price_df, monthly_amount, weights, rebalance, rebalance_freq):
    symbols = list(price_df.columns)
    w = {s: weights.get(s, 0.0) for s in symbols}

    shares = {s: 0.0 for s in symbols}
    total_invested = 0.0

    contrib_dates = get_monthly_contribution_dates(price_df.index)
    rb_dates = get_rebalance_dates(price_df.index, rebalance_freq) if rebalance else set()

    values = []
    invested_series = []

    for i, (dt, prices) in enumerate(price_df.iterrows()):
        if rebalance and dt in rb_dates and i > 0:
            total_val = sum(shares[s] * prices[s] for s in symbols)
            if total_val > 0:
                shares = {
                    s: total_val * w[s] / prices[s] if prices[s] > 0 else shares[s]
                    for s in symbols
                }

        if dt in contrib_dates:
            for s in symbols:
                if prices[s] > 0:
                    shares[s] += monthly_amount * w[s] / prices[s]
            total_invested += monthly_amount

        values.append(sum(shares[s] * prices[s] for s in symbols))
        invested_series.append(total_invested)

    return pd.Series(values, index=price_df.index), pd.Series(invested_series, index=price_df.index)


def build_price_df(ticker_data, bt_start, bt_end):
    """Align close prices across symbols and slice to selected period."""
    price_df_full = pd.DataFrame({s: data["df"]["Close"] for s, data in ticker_data.items()})
    price_df_full = price_df_full.ffill().dropna()

    bt_start_ts = pd.Timestamp(bt_start)
    bt_end_ts = pd.Timestamp(bt_end)
    price_df = price_df_full.loc[
        (price_df_full.index >= bt_start_ts) & (price_df_full.index <= bt_end_ts)
    ]
    return price_df_full, price_df


def get_unique_currencies(ticker_data):
    currencies = {s: data["info"].get("currency", "") for s, data in ticker_data.items()}
    return set(v for v in currencies.values() if v)


def build_plot_price_df(price_df, normalize):
    plot_price_df = price_df.copy()
    if normalize:
        base = plot_price_df.iloc[0].replace(0, np.nan)
        plot_price_df = plot_price_df.divide(base, axis=1) * 100
    return plot_price_df


def run_backtest(price_df, invest_type, initial_amount, monthly_amount, weights, rebalance, rebalance_freq):
    if invest_type == "거치식":
        return run_lump_sum(price_df, initial_amount, weights, rebalance, rebalance_freq)
    return run_dca(price_df, monthly_amount, weights, rebalance, rebalance_freq)


def trim_leading_zeros(portfolio, invested):
    non_zero = portfolio[portfolio > 0]
    if non_zero.empty:
        return None, None
    return portfolio[non_zero.index[0]:], invested[non_zero.index[0]:]


def compute_cagr(start_val, end_val, years):
    if start_val <= 0 or end_val <= 0 or years <= 0:
        return float("nan")
    return (end_val / start_val) ** (1 / years) - 1


def compute_max_drawdown(series):
    roll_max = series.cummax()
    drawdown = (series - roll_max) / roll_max
    return float(drawdown.min())


def compute_performance_metrics(portfolio, invested, invest_type, initial_amount, monthly_amount):
    years = (portfolio.index[-1] - portfolio.index[0]).days / 365.25
    final_value = portfolio.iloc[-1]
    total_invested_amount = invested.iloc[-1]
    total_return_pct = (
        (final_value - total_invested_amount) / total_invested_amount * 100
        if total_invested_amount > 0 else float("nan")
    )

    cagr_start = float(initial_amount) if invest_type == "거치식" else float(monthly_amount)
    cagr_pct = compute_cagr(cagr_start, final_value, years) * 100
    cagr_label = "연평균 수익률 (CAGR)" if invest_type == "거치식" else "연평균 수익률 (CAGR, 근사)"
    mdd_pct = compute_max_drawdown(portfolio) * 100

    return {
        "years": years,
        "final_value": final_value,
        "total_invested_amount": total_invested_amount,
        "total_return_pct": total_return_pct,
        "cagr_pct": cagr_pct,
        "cagr_label": cagr_label,
        "mdd_pct": mdd_pct,
    }
