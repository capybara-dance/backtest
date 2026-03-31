import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
import pandas as pd
import numpy as np
from datetime import date, timedelta

st.set_page_config(page_title="주가 백테스트", page_icon="📈", layout="wide")

st.title("📈 주가 백테스트")
st.write("여러 종목을 입력하고, 투자 방식·비중·리밸런싱 설정을 통해 포트폴리오 백테스트를 실행하세요.")

# ============================================================
# Ticker input
# ============================================================
raw_input = st.text_input(
    "티커 심볼 입력 (쉼표로 구분, 예: AAPL, MSFT, 005930.KS)",
    value="AAPL, MSFT",
)

# Parse comma-separated tickers, deduplicate while preserving order
tickers = list(dict.fromkeys(
    t for t in (s.strip().upper() for s in raw_input.split(",")) if t
))

if not tickers:
    st.stop()

end_date = date.today()
start_date = end_date - timedelta(days=365 * 10)

# ============================================================
# Data fetch
# ============================================================
ticker_data = {}  # symbol -> {"df": df, "info": info}
failed = []

with st.spinner(f"{', '.join(tickers)} 데이터를 불러오는 중..."):
    for symbol in tickers:
        try:
            ticker_obj = yf.Ticker(symbol)
            df = ticker_obj.history(start=start_date, end=end_date)
            if df.empty:
                failed.append((symbol, "데이터 없음"))
                continue
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

    st.subheader("📊 종목별 투자 비중 (%)")
    n = len(ticker_data)
    default_w = round(100 / n, 1)
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
# Align price data across tickers
# ============================================================
price_df = pd.DataFrame({s: data["df"]["Close"] for s, data in ticker_data.items()})
price_df = price_df.ffill().dropna()

if price_df.empty:
    st.error("선택한 종목들 사이에 겹치는 거래일이 없습니다.")
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
# Price chart
# ============================================================
st.subheader("📈 종가 비교 차트 (최근 10년)")
fig_price = go.Figure()
for symbol, data in ticker_data.items():
    df = data["df"]
    info = data["info"]
    label = info.get("longName") or info.get("shortName") or symbol
    fig_price.add_trace(
        go.Scatter(
            x=df.index,
            y=df["Close"],
            mode="lines",
            name=f"{label} ({symbol})",
            line=dict(width=1.5),
        )
    )
fig_price.update_layout(
    xaxis_title="날짜",
    yaxis_title="종가",
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

with st.expander("📐 적용된 투자 비중"):
    w_df = pd.DataFrame({
        "종목": list(weights.keys()),
        "비중": [f"{v * 100:.1f}%" for v in weights.values()],
    })
    st.dataframe(w_df, use_container_width=True, hide_index=True)

# ============================================================
# Per-ticker details
# ============================================================
st.subheader("📊 종목별 상세 정보")
tabs = st.tabs(list(ticker_data.keys()))
for tab, (symbol, data) in zip(tabs, ticker_data.items()):
    with tab:
        df = data["df"]
        info = data["info"]
        company_name = info.get("longName") or info.get("shortName") or symbol
        currency = info.get("currency", "")

        st.markdown(f"**{company_name}** ({symbol})")

        close = df["Close"]
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("조회 기간", f"{df.index[0].date()} ~ {df.index[-1].date()}")
        col2.metric("거래일 수", f"{len(df):,}일")
        col3.metric("최고 종가", f"{close.max():,.2f} {currency}")
        col4.metric("최저 종가", f"{close.min():,.2f} {currency}")

        col5, col6, col7, col8 = st.columns(4)
        total_return_ticker = (
            (close.iloc[-1] / close.iloc[0] - 1) * 100 if close.iloc[0] > 0 else float("nan")
        )
        col5.metric("시작 종가", f"{close.iloc[0]:,.2f} {currency}")
        col6.metric("최근 종가", f"{close.iloc[-1]:,.2f} {currency}")
        col7.metric("10년 수익률", f"{total_return_ticker:+.2f}%" if not pd.isna(total_return_ticker) else "N/A")
        col8.metric("평균 종가", f"{close.mean():,.2f} {currency}")

        st.markdown("**📋 기초 통계**")
        stats = (
            close.describe()
            .rename({
                "count": "데이터 수",
                "mean": "평균",
                "std": "표준편차",
                "min": "최솟값",
                "25%": "1사분위수",
                "50%": "중앙값",
                "75%": "3사분위수",
                "max": "최댓값",
            })
            .to_frame(name="종가")
        )
        st.dataframe(stats.style.format("{:,.4f}"), use_container_width=True)

        with st.expander("원본 데이터 보기"):
            st.dataframe(
                df[["Open", "High", "Low", "Close", "Volume"]].sort_index(ascending=False),
                use_container_width=True,
            )

