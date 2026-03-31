import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
import pandas as pd
from datetime import date, timedelta

st.set_page_config(page_title="주가 백테스트", page_icon="📈", layout="wide")

st.title("📈 주가 조회")
st.write("종목 티커를 쉼표로 구분하여 여러 개 입력하면 최근 10년간의 종가 데이터를 조회하고 차트를 표시합니다.")

raw_input = st.text_input(
    "티커 심볼 입력 (쉼표로 구분, 예: AAPL, MSFT, 005930.KS)",
    value="AAPL, MSFT",
)

# Parse comma-separated tickers, deduplicate while preserving order
tickers = list(dict.fromkeys(
    t for t in (s.strip().upper() for s in raw_input.split(",")) if t
))

if tickers:
    end_date = date.today()
    start_date = end_date - timedelta(days=365 * 10)

    # --- Fetch data for all tickers ---
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

    # --- Combined closing price chart ---
    st.subheader("📈 종가 비교 차트 (최근 10년)")
    fig = go.Figure()
    for symbol, data in ticker_data.items():
        df = data["df"]
        info = data["info"]
        label = info.get("longName") or info.get("shortName") or symbol
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["Close"],
                mode="lines",
                name=f"{label} ({symbol})",
                line=dict(width=1.5),
            )
        )
    fig.update_layout(
        xaxis_title="날짜",
        yaxis_title="종가",
        hovermode="x unified",
        height=520,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)

    # --- Per-ticker details in tabs ---
    st.subheader("📊 종목별 상세 정보")
    tabs = st.tabs([f"{s}" for s in ticker_data])
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
            total_return = (close.iloc[-1] / close.iloc[0] - 1) * 100 if close.iloc[0] > 0 else float("nan")
            col5.metric("시작 종가", f"{close.iloc[0]:,.2f} {currency}")
            col6.metric("최근 종가", f"{close.iloc[-1]:,.2f} {currency}")
            col7.metric("10년 수익률", f"{total_return:+.2f}%" if not pd.isna(total_return) else "N/A")
            col8.metric("평균 종가", f"{close.mean():,.2f} {currency}")

            st.markdown("**📋 기초 통계**")
            stats = (
                close.describe()
                .rename(
                    {
                        "count": "데이터 수",
                        "mean": "평균",
                        "std": "표준편차",
                        "min": "최솟값",
                        "25%": "1사분위수",
                        "50%": "중앙값",
                        "75%": "3사분위수",
                        "max": "최댓값",
                    }
                )
                .to_frame(name="종가")
            )
            st.dataframe(stats.style.format("{:,.4f}"), use_container_width=True)

            with st.expander("원본 데이터 보기"):
                st.dataframe(
                    df[["Open", "High", "Low", "Close", "Volume"]].sort_index(ascending=False),
                    use_container_width=True,
                )
