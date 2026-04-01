from datetime import date
from html import escape

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


def _apply_dividend_reinvestment(shares, symbols, divs, prices):
    """Reinvest dividends by buying additional shares of the same symbol.

    For each symbol, if a dividend per share was paid on this date, the total
    dividend cash (shares * dividend_per_share) is immediately used to purchase
    additional shares at the current closing price.
    """
    for s in symbols:
        div_per_share = divs.get(s, 0.0)
        if div_per_share > 0 and prices[s] > 0:
            shares[s] += shares[s] * div_per_share / prices[s]


def run_lump_sum(price_df, initial_amount, weights, rebalance, rebalance_freq,
                 dividend_df=None, reinvest_dividends=True):
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

        if reinvest_dividends and dividend_df is not None and dt in dividend_df.index:
            _apply_dividend_reinvestment(shares, symbols, dividend_df.loc[dt], prices)

        values.append(sum(shares[s] * prices[s] for s in symbols))

    portfolio = pd.Series(values, index=price_df.index)
    invested = pd.Series(float(initial_amount), index=price_df.index)
    return portfolio, invested


def run_dca(price_df, monthly_amount, weights, rebalance, rebalance_freq,
            dividend_df=None, reinvest_dividends=True):
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

        if reinvest_dividends and dividend_df is not None and dt in dividend_df.index:
            _apply_dividend_reinvestment(shares, symbols, dividend_df.loc[dt], prices)

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


def build_dividend_df(ticker_data, bt_start, bt_end):
    """Align dividend amounts across symbols and slice to selected period.

    Returns a DataFrame with the same index as price_df where each cell
    contains the dividend-per-share paid on that date (0 when none).
    """
    div_frames = {}
    for s, data in ticker_data.items():
        df = data["df"]
        if "Dividends" in df.columns:
            div_frames[s] = df["Dividends"]
        else:
            div_frames[s] = pd.Series(0.0, index=df.index)

    dividend_df = pd.DataFrame(div_frames).fillna(0.0)

    bt_start_ts = pd.Timestamp(bt_start)
    bt_end_ts = pd.Timestamp(bt_end)
    dividend_df = dividend_df.loc[
        (dividend_df.index >= bt_start_ts) & (dividend_df.index <= bt_end_ts)
    ]
    return dividend_df


def get_unique_currencies(ticker_data):
    currencies = {s: data["info"].get("currency", "") for s, data in ticker_data.items()}
    return set(v for v in currencies.values() if v)


def build_plot_price_df(price_df, normalize):
    plot_price_df = price_df.copy()
    if normalize:
        base = plot_price_df.iloc[0].replace(0, np.nan)
        plot_price_df = plot_price_df.divide(base, axis=1) * 100
    return plot_price_df


def run_backtest(price_df, invest_type, initial_amount, monthly_amount, weights, rebalance, rebalance_freq,
                 dividend_df=None, reinvest_dividends=True):
    if invest_type == "거치식":
        return run_lump_sum(price_df, initial_amount, weights, rebalance, rebalance_freq,
                            dividend_df=dividend_df, reinvest_dividends=reinvest_dividends)
    return run_dca(price_df, monthly_amount, weights, rebalance, rebalance_freq,
                   dividend_df=dividend_df, reinvest_dividends=reinvest_dividends)


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


def _fmt_pct(value):
        if pd.isna(value):
                return "N/A"
        return f"{value:+.2f}%"


def _fmt_num(value):
        if pd.isna(value):
                return "N/A"
        return f"{value:,.0f}"


def build_html_report(
        actual_start,
        actual_end,
        tickers,
        weights,
        invest_type,
        initial_amount,
        monthly_amount,
        rebalance,
        rebalance_freq,
        reinvest_dividends,
        normalize_price_chart,
        unique_currencies,
        metrics,
        fig_price_html,
        fig_bt_html,
):
        """Build a standalone HTML report for the current backtest run."""
        strategy_desc = (
                "거치식 전략: 시작 시점에 목표 비중대로 일괄 매수하고, 선택 시 리밸런싱 주기에 맞춰 비중을 재조정합니다."
                if invest_type == "거치식"
                else "적립식 전략: 매월 첫 거래일에 월 투자금을 목표 비중대로 분할 매수하고, 선택 시 리밸런싱을 수행합니다."
        )

        rebalance_desc = "미적용"
        if rebalance:
                rebalance_desc = f"적용 ({rebalance_freq})"

        currency_desc = ", ".join(sorted(unique_currencies)) if unique_currencies else "정보 없음"
        weights_rows = "".join(
                f"<tr><td>{escape(symbol)}</td><td>{weights.get(symbol, 0.0) * 100:.2f}%</td></tr>"
                for symbol in tickers
        )

        config_rows = [
                ("백테스트 기간", f"{actual_start} ~ {actual_end}"),
                ("투자 방식", invest_type),
                ("초기 투자금", f"₩{initial_amount:,.0f}" if invest_type == "거치식" else "해당 없음"),
                ("월 투자금", f"₩{monthly_amount:,.0f}" if invest_type == "적립식" else "해당 없음"),
                ("리밸런싱", rebalance_desc),
                ("배당 재투자", "적용" if reinvest_dividends else "미적용"),
                ("종가 비교 정규화", "ON (시작값=100)" if normalize_price_chart else "OFF"),
                ("통화", currency_desc),
        ]

        config_table_rows = "".join(
                f"<tr><th>{escape(k)}</th><td>{escape(str(v))}</td></tr>" for k, v in config_rows
        )

        result_rows = [
                ("총 투자금", f"₩{_fmt_num(metrics['total_invested_amount'])}"),
                ("최종 평가액", f"₩{_fmt_num(metrics['final_value'])}"),
                ("총 수익률", _fmt_pct(metrics["total_return_pct"])),
                (metrics["cagr_label"], _fmt_pct(metrics["cagr_pct"])),
                ("최대 낙폭 (MDD)", _fmt_pct(metrics["mdd_pct"])),
        ]

        result_table_rows = "".join(
                f"<tr><th>{escape(k)}</th><td>{escape(str(v))}</td></tr>" for k, v in result_rows
        )

        return f"""<!doctype html>
<html lang=\"ko\">
<head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Capybara Backtest Report</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Arial, sans-serif; margin: 24px; color: #1f2937; }}
        h1, h2 {{ margin: 0 0 12px 0; }}
        .section {{ margin-top: 28px; }}
        .card {{ background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; }}
        table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
        th, td {{ border: 1px solid #e5e7eb; padding: 10px; text-align: left; }}
        th {{ background: #f3f4f6; width: 260px; }}
        .muted {{ color: #6b7280; }}
    </style>
</head>
<body>
    <h1>Capybara Backtest Report</h1>
    <p class=\"muted\">대상 종목: {escape(', '.join(tickers))}</p>

    <div class=\"section\">
        <h2>전략 설명</h2>
        <div class=\"card\">{escape(strategy_desc)}</div>
    </div>

    <div class=\"section\">
        <h2>적용된 설정</h2>
        <table>
            {config_table_rows}
        </table>
    </div>

    <div class=\"section\">
        <h2>종목별 비중</h2>
        <table>
            <tr><th>종목</th><th>비중</th></tr>
            {weights_rows}
        </table>
    </div>

    <div class=\"section\">
        <h2>결과 요약</h2>
        <table>
            {result_table_rows}
        </table>
    </div>

    <div class=\"section\">
        <h2>종가 비교 차트</h2>
        {fig_price_html}
    </div>

    <div class=\"section\">
        <h2>포트폴리오 백테스트 차트</h2>
        {fig_bt_html}
    </div>
</body>
</html>
"""
