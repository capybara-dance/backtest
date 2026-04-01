import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
import pandas as pd
import numpy as np
from datetime import date, timedelta

st.set_page_config(page_title="Capybara Backtest", page_icon="🦫", layout="wide")

st.title("Capybara Backtest 🦫")
st.write("여러 종목을 입력하고, 투자 방식·기간·비중·리밸런싱 설정을 통해 포트폴리오 백테스트를 실행하세요.")

# ============================================================
# Ticker input
# ============================================================
raw_input = st.text_input(
    "티커 심볼 입력 (쉼표로 구분, 예: AAPL, MSFT, 005930.KS)",
    value="278530.KS, 455030.KS, 379800.KS",
)

# Parse comma-separated tickers, deduplicate while preserving order
tickers = list(dict.fromkeys(
    t for t in (s.strip().upper() for s in raw_input.split(",")) if t
))

if not tickers:
    st.stop()

# Always fetch max 10 years of raw data so the user can pick any sub-range
_fetch_end = date.today()
_fetch_start = _fetch_end - timedelta(days=365 * 10)

# ============================================================
# Data fetch
# ============================================================
ticker_data = {}  # symbol -> {"df": df, "info": info}
failed = []

with st.spinner(f"{', '.join(tickers)} 데이터를 불러오는 중..."):
    for symbol in tickers:
        try:
            ticker_obj = yf.Ticker(symbol)
            df = ticker_obj.history(start=_fetch_start, end=_fetch_end)
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

for symbol, reason in failed:
    st.warning(f"'{symbol}': {reason}. 올바른 티커 심볼을 입력해 주세요.")

if not ticker_data:
    st.stop()

# ============================================================
# Compute default date bounds from fetched data
# ============================================================
# Filter out any NaT entries before calling .date()
def _safe_date(ts):
    return ts.date() if not pd.isnull(ts) else None

# Start = latest of all tickers' first available date (ensures all tickers have data)
data_start_default = max(
    _safe_date(data["df"].index[0]) for data in ticker_data.values()
    if not data["df"].empty and _safe_date(data["df"].index[0]) is not None
)
# Earliest possible start = the oldest first date across tickers
data_start_min = min(
    _safe_date(data["df"].index[0]) for data in ticker_data.values()
    if not data["df"].empty and _safe_date(data["df"].index[0]) is not None
)
# Latest possible end = most recent date available in fetched data
data_end_max = max(
    _safe_date(data["df"].index[-1]) for data in ticker_data.values()
    if not data["df"].empty and _safe_date(data["df"].index[-1]) is not None
)
data_end_default = min(date.today(), data_end_max)

# ============================================================
# Sidebar: investment parameters
# ============================================================
with st.sidebar:
    st.header("⚙️ 투자 설정")

    invest_type = st.radio("투자 방식", ["거치식", "적립식"])

    if invest_type == "거치식":
        initial_amount = st.number_input(
            "초기 투자금 (원)",
            min_value=10_000,
            max_value=10_000_000_000,
            value=10_000_000,
            step=1_000_000,
            format="%d",
        )
        monthly_amount = 0
    else:
        monthly_amount = st.number_input(
            "월 투자금액 (원)",
            min_value=10_000,
            max_value=100_000_000,
            value=1_000_000,
            step=100_000,
            format="%d",
        )
        initial_amount = 0

    st.subheader("📅 백테스트 기간")
    bt_start = st.date_input(
        "시작 날짜",
        value=data_start_default,
        min_value=data_start_min,
        max_value=data_end_max,
    )
    bt_end = st.date_input(
        "종료 날짜",
        value=data_end_default,
        min_value=data_start_min + timedelta(days=1),
        max_value=data_end_max,
    )
    if bt_start >= bt_end:
        st.error("시작 날짜는 종료 날짜보다 앞이어야 합니다.")
        st.stop()

    st.subheader("📊 종목별 투자 비중 (%)")
    n = len(ticker_data)
    default_w = round(100 / n, 1)

    # When ticker composition changes, reset weights to equal allocation (1/N).
    current_symbols = tuple(ticker_data.keys())
    prev_symbols = st.session_state.get("weight_symbols")
    if prev_symbols != current_symbols:
        for symbol in ticker_data:
            st.session_state[f"w_{symbol}"] = default_w
        st.session_state["weight_symbols"] = current_symbols

    raw_weights = {}
    for symbol in ticker_data:
        raw_weights[symbol] = st.number_input(
            symbol,
            min_value=0.0,
            max_value=100.0,
            value=default_w,
            step=0.1,
            format="%.1f",
            key=f"w_{symbol}",
        )

    total_w = sum(raw_weights.values())
    if total_w > 0:
        weights = {s: v / total_w for s, v in raw_weights.items()}
        if abs(total_w - 100) > 0.1:
            st.warning(f"비중 합계 {total_w:.1f}% → 자동 정규화됩니다.")
        else:
            st.success(f"비중 합계: {total_w:.1f}%")
    else:
        weights = {s: 1 / n for s in ticker_data}
        st.warning("비중을 입력해 주세요.")

    st.subheader("🔄 리밸런싱")
    rebalance = st.checkbox("리밸런싱 적용", value=False)
    rebalance_freq = None
    if rebalance:
        rebalance_freq = st.selectbox("리밸런싱 주기", ["월별", "분기별", "연간"])


# ============================================================
# Helper functions
# ============================================================
def get_rebalance_dates(index, freq):
    """Return the set of dates on which rebalancing occurs."""
    idx = pd.DatetimeIndex(index)
    freq_map = {"월별": "MS", "분기별": "QS", "연간": "YS"}
    rebal = pd.Series(idx, index=idx).resample(freq_map[freq]).first()
    return set(rebal.dropna())


def get_monthly_contribution_dates(index):
    """Return the first trading day of each month (for DCA contributions)."""
    idx = pd.DatetimeIndex(index)
    contrib = pd.Series(idx, index=idx).resample("MS").first()
    return set(contrib.dropna())


def compute_cagr(start_val, end_val, years):
    if start_val <= 0 or end_val <= 0 or years <= 0:
        return float("nan")
    return (end_val / start_val) ** (1 / years) - 1


def compute_max_drawdown(series):
    roll_max = series.cummax()
    drawdown = (series - roll_max) / roll_max
    return float(drawdown.min())


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
        # Rebalance existing holdings before this period's contribution
        if rebalance and dt in rb_dates and i > 0:
            total_val = sum(shares[s] * prices[s] for s in symbols)
            if total_val > 0:
                shares = {
                    s: total_val * w[s] / prices[s] if prices[s] > 0 else shares[s]
                    for s in symbols
                }

        # Monthly contribution on first trading day of each month
        if dt in contrib_dates:
            for s in symbols:
                if prices[s] > 0:
                    shares[s] += monthly_amount * w[s] / prices[s]
            total_invested += monthly_amount

        values.append(sum(shares[s] * prices[s] for s in symbols))
        invested_series.append(total_invested)

    return pd.Series(values, index=price_df.index), pd.Series(invested_series, index=price_df.index)


# ============================================================
# Align & filter price data to user-selected date range
# ============================================================
price_df_full = pd.DataFrame({s: data["df"]["Close"] for s, data in ticker_data.items()})
price_df_full = price_df_full.ffill().dropna()

if price_df_full.empty:
    st.error("선택한 종목들 사이에 겹치는 거래일이 없습니다.")
    st.stop()

# Slice to user-selected backtest window
bt_start_ts = pd.Timestamp(bt_start)
bt_end_ts = pd.Timestamp(bt_end)
price_df = price_df_full.loc[
    (price_df_full.index >= bt_start_ts) & (price_df_full.index <= bt_end_ts)
]

if price_df.empty:
    st.error("선택한 기간에 해당하는 거래일 데이터가 없습니다. 기간을 조정해 주세요.")
    st.stop()

# Warn if currencies differ (prices are not directly comparable)
currencies = {s: data["info"].get("currency", "") for s, data in ticker_data.items()}
unique_currencies = set(v for v in currencies.values() if v)
if len(unique_currencies) > 1:
    st.info(
        f"⚠️ 종목들의 통화가 다릅니다 ({', '.join(unique_currencies)}). "
        "백테스트 수치는 환율을 고려하지 않은 참고용입니다."
    )

# ============================================================
# Price chart (filtered to backtest period)
# ============================================================
actual_start = price_df.index[0].date()
actual_end = price_df.index[-1].date()
st.subheader(f"📈 종가 비교 차트 ({actual_start} ~ {actual_end})")
normalize_price_chart = st.checkbox(
    "정규화 비교 (시작값=100)",
    value=True,
    help="활성화하면 선택 기간 시작일 종가를 100으로 맞춰 종목 간 상대 성과를 비교합니다.",
)

plot_price_df = price_df.copy()
if normalize_price_chart:
    # Avoid division-by-zero for malformed data; affected points become NaN.
    base = plot_price_df.iloc[0].replace(0, np.nan)
    plot_price_df = plot_price_df.divide(base, axis=1) * 100

fig_price = go.Figure()
for symbol, data in ticker_data.items():
    info = data["info"]
    label = info.get("longName") or info.get("shortName") or symbol
    fig_price.add_trace(
        go.Scatter(
            x=plot_price_df.index,
            y=plot_price_df[symbol].values,
            mode="lines",
            name=f"{label} ({symbol})",
            line=dict(width=1.5),
        )
    )
fig_price.update_layout(
    xaxis_title="날짜",
    yaxis_title="정규화 지수 (시작값=100)" if normalize_price_chart else "종가",
    hovermode="x unified",
    height=450,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)
st.plotly_chart(fig_price, use_container_width=True)

# ============================================================
# Backtest
# ============================================================
st.subheader("💼 백테스트 결과")

if invest_type == "거치식":
    portfolio, invested = run_lump_sum(price_df, initial_amount, weights, rebalance, rebalance_freq)
else:
    portfolio, invested = run_dca(price_df, monthly_amount, weights, rebalance, rebalance_freq)

# Trim leading zeros (DCA: before first contribution)
non_zero = portfolio[portfolio > 0]
if non_zero.empty:
    st.warning("포트폴리오 데이터가 없습니다.")
    st.stop()

portfolio = portfolio[non_zero.index[0]:]
invested = invested[non_zero.index[0]:]

years = (portfolio.index[-1] - portfolio.index[0]).days / 365.25
final_value = portfolio.iloc[-1]
total_invested_amount = invested.iloc[-1]
total_return_pct = (
    (final_value - total_invested_amount) / total_invested_amount * 100
    if total_invested_amount > 0 else float("nan")
)
# CAGR: for lump-sum use initial_amount as the true start value;
# for DCA it is an approximation (assumes all capital deployed at t=0).
cagr_start = float(initial_amount) if invest_type == "거치식" else float(monthly_amount)
cagr_pct = compute_cagr(cagr_start, final_value, years) * 100
cagr_label = "연평균 수익률 (CAGR)" if invest_type == "거치식" else "연평균 수익률 (CAGR, 근사)"
mdd_pct = compute_max_drawdown(portfolio) * 100

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("총 투자금", f"₩{total_invested_amount:,.0f}")
col2.metric("최종 평가액", f"₩{final_value:,.0f}")
col3.metric("총 수익률", f"{total_return_pct:+.2f}%" if not np.isnan(total_return_pct) else "N/A")
col4.metric(cagr_label, f"{cagr_pct:+.2f}%" if not np.isnan(cagr_pct) else "N/A")
col5.metric("최대 낙폭 (MDD)", f"{mdd_pct:.2f}%")

if invest_type == "적립식":
    st.caption(
        "ℹ️ 적립식 CAGR은 첫 달 투자금을 기준으로 한 근사값입니다. "
        "정확한 수익률은 내부수익률(IRR) 방식으로 계산해야 합니다."
    )

fig_bt = go.Figure()
fig_bt.add_trace(go.Scatter(
    x=portfolio.index,
    y=portfolio.values,
    mode="lines",
    name="포트폴리오 가치",
    line=dict(color="#2ca02c", width=2),
    fill="tozeroy",
    fillcolor="rgba(44, 160, 44, 0.1)",
))
fig_bt.add_trace(go.Scatter(
    x=invested.index,
    y=invested.values,
    mode="lines",
    name="누적 투자금",
    line=dict(color="#ff7f0e", width=1.5, dash="dash"),
))
fig_bt.update_layout(
    xaxis_title="날짜",
    yaxis_title="금액 (원)",
    hovermode="x unified",
    height=450,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)
st.plotly_chart(fig_bt, use_container_width=True)

