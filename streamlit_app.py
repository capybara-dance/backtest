import plotly.graph_objects as go
from plotly.colors import qualitative
import streamlit as st
from datetime import date, timedelta
from math import isnan

from backtest_logic import (
    build_plot_price_df,
    build_price_df,
    build_html_report,
    compute_date_bounds,
    compute_performance_metrics,
    fetch_ticker_data,
    get_unique_currencies,
    normalize_weights,
    parse_tickers,
    run_backtest,
    trim_leading_zeros,
)

st.set_page_config(page_title="Capybara Backtest", layout="wide")

st.markdown(
    """
    <style>
      @media (max-width: 768px) {
        .block-container {
          padding-top: 1rem;
          padding-left: 0.8rem;
          padding-right: 0.8rem;
          padding-bottom: 1rem;
        }
        h1 {
          font-size: 1.55rem !important;
          line-height: 1.25 !important;
        }
        div[data-testid="stMetricValue"] {
          font-size: 1.2rem !important;
        }
        div[data-testid="stMetricLabel"] {
          font-size: 0.85rem !important;
        }
      }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Capybara Backtest")
st.write("여러 종목을 입력하고, 투자 방식·기간·비중·리밸런싱 설정을 통해 포트폴리오 백테스트를 실행하세요.")
st.caption("모바일에서는 좌상단 메뉴에서 사이드바를 열어 투자 설정을 바꿀 수 있습니다.")

raw_input = st.text_input(
    "티커 심볼 입력 (쉼표로 구분, 예: AAPL, MSFT, 005930.KS)",
    value="278530.KS, 455030.KS, 379800.KS",
)

tickers = parse_tickers(raw_input)
if not tickers:
    st.stop()

fetch_end = date.today()
fetch_start = fetch_end - timedelta(days=365 * 10)

with st.spinner(f"{', '.join(tickers)} 데이터를 불러오는 중..."):
    ticker_data, failed = fetch_ticker_data(tickers, fetch_start, fetch_end)

for symbol, reason in failed:
    st.warning(f"'{symbol}': {reason}. 올바른 티커 심볼을 입력해 주세요.")

if not ticker_data:
    st.stop()

(
    data_start_default,
    data_start_min,
    data_end_max,
    data_end_default,
) = compute_date_bounds(ticker_data)

current_symbols = tuple(ticker_data.keys())
prev_period_symbols = st.session_state.get("period_symbols")
if prev_period_symbols != current_symbols:
    st.session_state["bt_start"] = data_start_default
    st.session_state["bt_end"] = data_end_default
    st.session_state["period_symbols"] = current_symbols

if "bt_start" not in st.session_state:
    st.session_state["bt_start"] = data_start_default
if "bt_end" not in st.session_state:
    st.session_state["bt_end"] = data_end_default

st.session_state["bt_start"] = min(max(st.session_state["bt_start"], data_start_min), data_end_max)
st.session_state["bt_end"] = min(max(st.session_state["bt_end"], data_start_min + timedelta(days=1)), data_end_max)

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
        key="bt_start",
        min_value=data_start_min,
        max_value=data_end_max,
    )
    end_min = max(data_start_min + timedelta(days=1), bt_start + timedelta(days=1))
    if st.session_state["bt_end"] < end_min:
        st.session_state["bt_end"] = end_min
    bt_end = st.date_input(
        "종료 날짜",
        key="bt_end",
        min_value=end_min,
        max_value=data_end_max,
    )
    if bt_start >= bt_end:
        st.error("시작 날짜는 종료 날짜보다 앞이어야 합니다.")
        st.stop()

    st.subheader("📊 종목별 투자 비중 (%)")
    symbols = list(ticker_data.keys())
    n = len(symbols)
    default_w = round(100 / n, 1)

    current_symbols = tuple(symbols)
    prev_symbols = st.session_state.get("weight_symbols")
    if prev_symbols != current_symbols:
        for symbol in symbols:
            st.session_state[f"w_{symbol}"] = default_w
        st.session_state["weight_symbols"] = current_symbols

    raw_weights = {}
    for symbol in symbols:
        raw_weights[symbol] = st.number_input(
            symbol,
            min_value=0.0,
            max_value=100.0,
            value=default_w,
            step=0.1,
            format="%.1f",
            key=f"w_{symbol}",
        )

    weights, total_w = normalize_weights(raw_weights, symbols)
    if total_w > 0:
        if abs(total_w - 100) > 0.1:
            st.warning(f"비중 합계 {total_w:.1f}% → 자동 정규화됩니다.")
        else:
            st.success(f"비중 합계: {total_w:.1f}%")
    else:
        st.warning("비중을 입력해 주세요.")

    st.subheader("🔄 리밸런싱")
    rebalance = st.checkbox("리밸런싱 적용", value=False)
    rebalance_freq = st.selectbox("리밸런싱 주기", ["월별", "분기별", "연간"]) if rebalance else None

price_df_full, price_df = build_price_df(ticker_data, bt_start, bt_end)

if price_df_full.empty:
    st.error("선택한 종목들 사이에 겹치는 거래일이 없습니다.")
    st.stop()

if price_df.empty:
    st.error("선택한 기간에 해당하는 거래일 데이터가 없습니다. 기간을 조정해 주세요.")
    st.stop()

unique_currencies = get_unique_currencies(ticker_data)
if len(unique_currencies) > 1:
    st.info(
        f"⚠️ 종목들의 통화가 다릅니다 ({', '.join(unique_currencies)}). "
        "백테스트 수치는 환율을 고려하지 않은 참고용입니다."
    )

actual_start = price_df.index[0].date()
actual_end = price_df.index[-1].date()
st.subheader(f"📈 종가 비교 차트 ({actual_start} ~ {actual_end})")
normalize_price_chart = st.checkbox(
    "정규화 비교 (시작값=100)",
    value=True,
    help="활성화하면 선택 기간 시작일 종가를 100으로 맞춰 종목 간 상대 성과를 비교합니다.",
)

plot_price_df = build_plot_price_df(price_df, normalize_price_chart)

fig_price = go.Figure()
palette = qualitative.Plotly
for i, (symbol, data) in enumerate(ticker_data.items()):
    info = data["info"]
    label = info.get("longName") or info.get("shortName") or symbol
    fig_price.add_trace(
        go.Scatter(
            x=plot_price_df.index,
            y=plot_price_df[symbol].values,
            mode="lines",
            name=f"{label} ({symbol})",
            line=dict(width=2, color=palette[i % len(palette)]),
        )
    )

fig_price.update_layout(
    xaxis_title="날짜",
    yaxis_title="정규화 지수 (시작값=100)" if normalize_price_chart else "종가",
    hovermode="x unified",
    height=380,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)
st.plotly_chart(fig_price, use_container_width=True)

st.subheader("💼 백테스트 결과")
portfolio, invested = run_backtest(
    price_df,
    invest_type,
    initial_amount,
    monthly_amount,
    weights,
    rebalance,
    rebalance_freq,
)

portfolio, invested = trim_leading_zeros(portfolio, invested)
if portfolio is None:
    st.warning("포트폴리오 데이터가 없습니다.")
    st.stop()

metrics = compute_performance_metrics(
    portfolio,
    invested,
    invest_type,
    initial_amount,
    monthly_amount,
)

row1_col1, row1_col2 = st.columns(2)
row1_col1.metric("총 투자금", f"₩{metrics['total_invested_amount']:,.0f}")
row1_col2.metric("최종 평가액", f"₩{metrics['final_value']:,.0f}")

row2_col1, row2_col2 = st.columns(2)
row2_col1.metric(
    "총 수익률",
    f"{metrics['total_return_pct']:+.2f}%" if not isnan(metrics["total_return_pct"]) else "N/A",
)
row2_col2.metric(
    metrics["cagr_label"],
    f"{metrics['cagr_pct']:+.2f}%" if not isnan(metrics["cagr_pct"]) else "N/A",
)

st.metric("최대 낙폭 (MDD)", f"{metrics['mdd_pct']:.2f}%")

if invest_type == "적립식":
    st.caption(
        "ℹ️ 적립식 CAGR은 첫 달 투자금을 기준으로 한 근사값입니다. "
        "정확한 수익률은 내부수익률(IRR) 방식으로 계산해야 합니다."
    )

fig_bt = go.Figure()
fig_bt.add_trace(
    go.Scatter(
        x=portfolio.index,
        y=portfolio.values,
        mode="lines",
        name="포트폴리오 가치",
        line=dict(color="#2ca02c", width=2),
        fill="tozeroy",
        fillcolor="rgba(44, 160, 44, 0.1)",
    )
)
fig_bt.add_trace(
    go.Scatter(
        x=invested.index,
        y=invested.values,
        mode="lines",
        name="누적 투자금",
        line=dict(color="#ff7f0e", width=1.5, dash="dash"),
    )
)
fig_bt.update_layout(
    xaxis_title="날짜",
    yaxis_title="금액 (원)",
    hovermode="x unified",
    height=380,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)
st.plotly_chart(fig_bt, use_container_width=True)

fig_price_html = fig_price.to_html(full_html=False, include_plotlyjs="cdn")
fig_bt_html = fig_bt.to_html(full_html=False, include_plotlyjs=False)
report_html = build_html_report(
    actual_start=actual_start,
    actual_end=actual_end,
    tickers=tickers,
    weights=weights,
    invest_type=invest_type,
    initial_amount=initial_amount,
    monthly_amount=monthly_amount,
    rebalance=rebalance,
    rebalance_freq=rebalance_freq,
    normalize_price_chart=normalize_price_chart,
    unique_currencies=unique_currencies,
    metrics=metrics,
    fig_price_html=fig_price_html,
    fig_bt_html=fig_bt_html,
)

st.download_button(
    label="📄 HTML 리포트 다운로드",
    data=report_html.encode("utf-8"),
    file_name=f"capybara_backtest_report_{actual_end}.html",
    mime="text/html",
)
