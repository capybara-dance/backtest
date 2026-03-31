import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
import pandas as pd
from datetime import date, timedelta

st.set_page_config(page_title="주가 백테스트", page_icon="📈", layout="wide")

st.title("📈 주가 조회")
st.write("종목 티커를 입력하면 최근 10년간의 종가 데이터를 조회하고 차트를 표시합니다.")

ticker_input = st.text_input("티커 심볼 입력 (예: AAPL, MSFT, 005930.KS)", value="AAPL").strip().upper()

if ticker_input:
    end_date = date.today()
    start_date = end_date - timedelta(days=365 * 10)

    with st.spinner(f"{ticker_input} 데이터를 불러오는 중..."):
        try:
            ticker = yf.Ticker(ticker_input)
            df = ticker.history(start=start_date, end=end_date)
        except (ConnectionError, TimeoutError, OSError):
            st.error("데이터 서비스에 연결할 수 없습니다. 네트워크 상태를 확인하고 다시 시도해 주세요.")
            st.stop()
        except Exception:
            st.error("데이터를 불러오는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")
            st.stop()

    if df.empty:
        st.warning(f"'{ticker_input}'에 대한 데이터를 찾을 수 없습니다. 올바른 티커 심볼을 입력해 주세요.")
    else:
        try:
            info = ticker.info
        except Exception:
            info = {}
        company_name = info.get("longName") or info.get("shortName") or ticker_input
        currency = info.get("currency", "")

        st.subheader(f"{company_name} ({ticker_input})")

        # --- 종가 차트 ---
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["Close"],
                mode="lines",
                name="종가",
                line=dict(color="#1f77b4", width=1.5),
            )
        )
        fig.update_layout(
            title=f"{company_name} 종가 추이 (최근 10년)",
            xaxis_title="날짜",
            yaxis_title=f"종가 ({currency})" if currency else "종가",
            hovermode="x unified",
            height=500,
        )
        st.plotly_chart(fig, use_container_width=True)

        # --- 데이터 설명 ---
        st.subheader("📊 데이터 요약")

        close = df["Close"]
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("조회 기간", f"{df.index[0].date()} ~ {df.index[-1].date()}")
        col2.metric("거래일 수", f"{len(df):,}일")
        col3.metric("최고 종가", f"{close.max():,.2f} {currency}")
        col4.metric("최저 종가", f"{close.min():,.2f} {currency}")

        col5, col6, col7, col8 = st.columns(4)
        total_return = (close.iloc[-1] / close.iloc[0] - 1) * 100 if close.iloc[0] != 0 else float("nan")
        col5.metric("시작 종가", f"{close.iloc[0]:,.2f} {currency}")
        col6.metric("최근 종가", f"{close.iloc[-1]:,.2f} {currency}")
        col7.metric("10년 수익률", f"{total_return:+.2f}%" if not pd.isna(total_return) else "N/A")
        col8.metric("평균 종가", f"{close.mean():,.2f} {currency}")

        st.subheader("📋 기초 통계")
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
            st.dataframe(df[["Open", "High", "Low", "Close", "Volume"]].sort_index(ascending=False), use_container_width=True)
