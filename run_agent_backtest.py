"""
run_agent_backtest.py
연금 계좌 ETF 포트폴리오 최적화 Agent 백테스트 실행기

사용법:
    python run_agent_backtest.py [--turns N] [--session S]
    기본값: turns=100, session=1 (자동으로 기존 기록에서 추론)

데이터 소스:
    etf_prices.parquet — capybara_fetcher 릴리즈에서 추출된 국내 ETF 종가 데이터
    (인터넷 없이도 동작. 신규 데이터 필요 시 data_refresh.py 실행)
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# 백테스트 로직 임포트
sys.path.insert(0, str(Path(__file__).parent))
from backtest_logic import (
    run_lump_sum,
    compute_performance_metrics,
)

# ─────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
ETF_LIST_PATH = BASE_DIR / "etf_list.json"
ETF_PRICES_PATH = BASE_DIR / "etf_prices.parquet"
HISTORY_PATH = BASE_DIR / "test_history.json"

INITIAL_AMOUNT = 10_000_000   # 거치식 초기 투자금 (1천만원)
RISK_FREE_RATE = 0.03         # 무위험이자율 3%
MIN_YEARS = 3                 # 최소 백테스트 기간 (년)
MAX_ETF_PER_PORTFOLIO = 10

# 스코어링 가중치
W_CAGR = 0.35
W_MDD = 0.30
W_SHARPE = 0.20
W_CALMAR = 0.15

# ─────────────────────────────────────────────
# 사전 정의된 후보 포트폴리오 (세션 1 탐색 전략)
# ─────────────────────────────────────────────
# (name, [(code, weight), ...], rebalance_enabled, rebalance_freq, notes)
CANDIDATE_PORTFOLIOS = [
    # --- 국내+해외+채권 혼합 (데이터 2015-01-02~) ---
    (
        "클래식 혼합형 (국내200+나스닥100+국채10년)",
        [("069500", 0.35), ("133690", 0.25), ("148070", 0.40)],
        False, None,
        "국내주식(KODEX200) 35% + 미국나스닥100 25% + 국고채10년 40%. 안정적 혼합 기본형."
    ),
    (
        "글로벌 분산 3자산 균등",
        [("069500", 0.33), ("133690", 0.33), ("148070", 0.34)],
        True, "분기별",
        "국내200 + 미국나스닥100 + 국고채10년 균등 배분. 분기 리밸런싱."
    ),
    (
        "나스닥100 중심 성장형",
        [("133690", 0.50), ("069500", 0.20), ("148070", 0.30)],
        False, None,
        "미국나스닥100 50% + 국내200 20% + 국고채10년 30%."
    ),
    # --- 단일 자산 집중 ---
    (
        "미국나스닥100 단독 100%",
        [("133690", 1.0)],
        False, None,
        "TIGER 미국나스닥100 단독. 성장 기준선."
    ),
    (
        "국내 KOSPI200 단독 100%",
        [("069500", 1.0)],
        False, None,
        "KODEX 200 단독. 국내 기준선."
    ),
    # --- 주식+채권 변형 ---
    (
        "80/20 적극형 (나스닥100+국채10년)",
        [("133690", 0.80), ("148070", 0.20)],
        True, "연간",
        "미국나스닥100 80% + 국고채10년 20%. 연간 리밸런싱."
    ),
    (
        "반도체+국내주식+채권 테마형",
        [("091160", 0.30), ("133690", 0.30), ("069500", 0.20), ("148070", 0.20)],
        False, None,
        "KODEX 반도체 30% + 미국나스닥100 30% + 국내200 20% + 국채 20%."
    ),
    (
        "배당+가치 안정형",
        [("279530", 0.30), ("069500", 0.20), ("133690", 0.20), ("148070", 0.30)],
        True, "연간",
        "KODEX고배당주 30% + 국내+미국 주식 20%씩 + 채권 30%. 연간 리밸런싱."
    ),
    (
        "국채10년 단독 100%",
        [("148070", 1.0)],
        False, None,
        "KIWOOM 국고채10년 단독. 채권 기준선."
    ),
    (
        "60/40 고전형 (국내200+국채10년)",
        [("069500", 0.60), ("148070", 0.40)],
        True, "월별",
        "국내KOSPI200 60% + 국고채10년 40%. 월별 리밸런싱. 전통적 60/40."
    ),
]


# ─────────────────────────────────────────────
# 헬퍼 함수
# ─────────────────────────────────────────────

def load_history():
    if HISTORY_PATH.exists():
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_history(history):
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ETF 가격 데이터 전역 캐시 (최초 로드 후 재사용)
_ETF_PRICES: pd.DataFrame | None = None


def get_etf_prices() -> pd.DataFrame:
    """etf_prices.parquet 로드 (Date × ETF 코드 pivot)."""
    global _ETF_PRICES
    if _ETF_PRICES is None:
        _ETF_PRICES = pd.read_parquet(ETF_PRICES_PATH)
        _ETF_PRICES.index = pd.to_datetime(_ETF_PRICES.index)
        _ETF_PRICES = _ETF_PRICES.sort_index()
    return _ETF_PRICES


def compute_sharpe(portfolio: pd.Series, risk_free: float = RISK_FREE_RATE) -> float:
    """연간화 샤프지수."""
    daily_returns = portfolio.pct_change().dropna()
    if daily_returns.std() == 0:
        return float("nan")
    excess = daily_returns.mean() - risk_free / 252
    sharpe = excess / daily_returns.std() * np.sqrt(252)
    return float(sharpe)


def compute_volatility(portfolio: pd.Series) -> float:
    """연간화 변동성 (%)."""
    daily_returns = portfolio.pct_change().dropna()
    return float(daily_returns.std() * np.sqrt(252) * 100)


def compute_calmar(cagr_pct: float, mdd_pct: float) -> float:
    """Calmar Ratio = CAGR / |MDD|."""
    if mdd_pct == 0:
        return float("nan")
    return cagr_pct / abs(mdd_pct)


def percentile_rank(value, series):
    """value 가 series 안에서 차지하는 백분위수(0~100)."""
    arr = np.array([v for v in series if v is not None and not np.isnan(v)])
    if len(arr) == 0:
        return 50.0
    return float(np.sum(arr <= value) / len(arr) * 100)


def compute_scores(history: list) -> list:
    """전체 history 기반 백분위수 스코어 재계산."""
    if not history:
        return history

    cagrs = [r["metrics"]["cagr_pct"] for r in history]
    mdds = [-r["metrics"]["mdd_pct"] for r in history]    # mdd는 음수이므로 부호 반전 (낙폭 작을수록 좋음)
    sharpes = [r["metrics"]["sharpe_ratio"] for r in history]
    calmars = [r["metrics"]["calmar_ratio"] for r in history]

    for r in history:
        sc_cagr = percentile_rank(r["metrics"]["cagr_pct"], cagrs)
        sc_mdd = percentile_rank(-r["metrics"]["mdd_pct"], mdds)
        sc_sharpe = percentile_rank(r["metrics"]["sharpe_ratio"], sharpes)
        sc_calmar = percentile_rank(r["metrics"]["calmar_ratio"], calmars)
        r["score"] = round(
            W_CAGR * sc_cagr + W_MDD * sc_mdd + W_SHARPE * sc_sharpe + W_CALMAR * sc_calmar, 2
        )

    return history


def run_single_backtest(code_weight_list, rebalance, rebalance_freq, turn_id, session, turn, notes,
                        code_to_name):
    """
    단일 포트폴리오 백테스트 실행 (로컬 etf_prices.parquet 사용).

    Parameters
    ----------
    code_weight_list : list of (code, weight)
    rebalance : bool
    rebalance_freq : str or None
    turn_id : str  (예: "test_0001")
    session : int
    turn : int
    notes : str
    code_to_name : dict  code → name 매핑

    Returns
    -------
    dict (test record) or None (데이터 없음)
    """
    raw_weights = {c: w for c, w in code_weight_list}
    total_w = sum(raw_weights.values())

    prices_all = get_etf_prices()

    # 각 코드의 가격 시리즈 추출
    price_series = {}
    for code, _ in code_weight_list:
        if code not in prices_all.columns:
            print(f"  [SKIP] {code} 가격 데이터 없음")
            return None
        s = prices_all[code].dropna()
        if s.empty:
            print(f"  [SKIP] {code} 가격 시리즈 비어 있음")
            return None
        price_series[code] = s

    # 공통 기간 결정 (모든 ETF 데이터가 있는 기간)
    bt_start = max(s.index[0] for s in price_series.values())
    bt_end = min(s.index[-1] for s in price_series.values())

    years = (bt_end - bt_start).days / 365.25
    if years < MIN_YEARS:
        print(f"  [SKIP] 공통 기간 {years:.1f}년 < {MIN_YEARS}년 최소 요건")
        return None

    # 가격 DataFrame 구성 (공통 기간 슬라이싱)
    price_df = pd.DataFrame({
        code: s.loc[bt_start:bt_end]
        for code, s in price_series.items()
    }).ffill().dropna()

    if price_df.empty:
        print(f"  [SKIP] price_df 비어 있음")
        return None

    # 정규화된 비중
    sym_weights = {c: raw_weights[c] / total_w for c in price_df.columns}

    # 거치식 백테스트 (배당 데이터 없으므로 dividend_df=None)
    rebal_freq_str = rebalance_freq if rebalance else "월별"  # 미적용 시 dummy
    portfolio, invested = run_lump_sum(
        price_df,
        INITIAL_AMOUNT,
        sym_weights,
        rebalance,
        rebal_freq_str,
        dividend_df=None,
        reinvest_dividends=False,
    )

    # 성과 지표
    metrics = compute_performance_metrics(
        portfolio, invested, "거치식", INITIAL_AMOUNT, 0
    )

    sharpe = compute_sharpe(portfolio)
    volatility = compute_volatility(portfolio)
    calmar = compute_calmar(metrics["cagr_pct"], metrics["mdd_pct"])

    actual_start = str(price_df.index[0].date())
    actual_end = str(price_df.index[-1].date())

    etf_entries = []
    for code, w in code_weight_list:
        etf_entries.append({
            "code": code,
            "name": code_to_name.get(code, code),
            "weight": round(raw_weights[code] / total_w, 4),
        })

    record = {
        "id": turn_id,
        "session": session,
        "turn": turn,
        "triggered_by": "ai_agent",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "portfolio": {
            "etfs": etf_entries,
            "rebalancing": {
                "enabled": rebalance,
                "frequency": rebalance_freq,
            },
            "reinvest_dividends": False,
        },
        "backtest": {
            "start_date": actual_start,
            "end_date": actual_end,
            "invest_type": "거치식",
            "initial_amount": INITIAL_AMOUNT,
            "data_source": "etf_prices.parquet (capybara_fetcher)",
        },
        "metrics": {
            "cagr_pct": round(metrics["cagr_pct"], 4),
            "total_return_pct": round(metrics["total_return_pct"], 4),
            "mdd_pct": round(metrics["mdd_pct"], 4),
            "sharpe_ratio": round(sharpe, 4) if not np.isnan(sharpe) else None,
            "volatility_annual_pct": round(volatility, 4),
            "calmar_ratio": round(calmar, 4) if not np.isnan(calmar) else None,
            "years": round(metrics["years"], 2),
        },
        "score": 50.0,  # 나중에 재계산
        "notes": notes,
    }

    return record


def generate_insights_md(history: list, session: int, session_records: list, date_str: str) -> str:
    """insights MD 문자열 생성."""
    top_session = sorted(session_records, key=lambda r: r["score"], reverse=True)[:10]
    top_all = sorted(history, key=lambda r: r["score"], reverse=True)[:10]

    def fmt_etfs(rec):
        parts = [f"{e['name']}({e['weight']*100:.0f}%)" for e in rec["portfolio"]["etfs"]]
        return ", ".join(parts)

    def fmt_row(rank, rec):
        rb = rec["portfolio"]["rebalancing"]
        rb_str = f"{rb['frequency']}" if rb["enabled"] else "없음"
        return (
            f"| {rank} | {rec['id']} | {fmt_etfs(rec)} | "
            f"{rec['metrics']['cagr_pct']:.2f}% | {rec['metrics']['mdd_pct']:.2f}% | "
            f"{rec['metrics'].get('sharpe_ratio') or 'N/A'} | {rb_str} | {rec['score']:.1f} |"
        )

    session_rows = "\n".join(fmt_row(i + 1, r) for i, r in enumerate(top_session))
    all_rows = "\n".join(fmt_row(i + 1, r) for i, r in enumerate(top_all))

    # 간단한 인사이트 추출
    if top_session:
        best = top_session[0]
        insights = []

        # CAGR 분석
        cagrs = [r["metrics"]["cagr_pct"] for r in session_records]
        insights.append(
            f"이번 세션 평균 CAGR: {np.mean(cagrs):.2f}%, 최고: {max(cagrs):.2f}% ({top_session[0]['id']}), 최저: {min(cagrs):.2f}%"
        )

        # MDD 분석
        mdds = [abs(r["metrics"]["mdd_pct"]) for r in session_records]
        insights.append(
            f"이번 세션 평균 MDD: -{np.mean(mdds):.2f}%, 최소(최고): -{min(mdds):.2f}%, 최대: -{max(mdds):.2f}%"
        )

        # 리밸런싱 효과
        rb_yes = [r for r in session_records if r["portfolio"]["rebalancing"]["enabled"]]
        rb_no = [r for r in session_records if not r["portfolio"]["rebalancing"]["enabled"]]
        if rb_yes and rb_no:
            avg_score_yes = np.mean([r["score"] for r in rb_yes])
            avg_score_no = np.mean([r["score"] for r in rb_no])
            diff = avg_score_yes - avg_score_no
            rb_effect = "리밸런싱이 평균적으로 유리" if diff > 0 else "리밸런싱 없이 홀드가 평균적으로 유리"
            insights.append(
                f"리밸런싱 여부 비교: 리밸런싱 평균 스코어 {avg_score_yes:.1f} vs 비리밸런싱 {avg_score_no:.1f} → {rb_effect}"
            )

        # 상위 포트폴리오 공통 ETF
        top3_codes = set()
        for r in top_session[:3]:
            for e in r["portfolio"]["etfs"]:
                top3_codes.add(e["name"])
        insights.append(
            f"상위 3개 포트폴리오 구성 ETF: {', '.join(sorted(top3_codes))}"
        )
    else:
        insights = ["데이터 부족으로 인사이트 분석 불가"]

    insights_text = "\n".join(f"{i + 1}. {ins}" for i, ins in enumerate(insights))

    md = f"""# 연금 ETF 포트폴리오 분석 리포트

> 생성일: {date_str}  
> 작성자: AI Agent (run_agent_backtest.py)

---

## 세션 정보

- 세션 번호: {session}
- 이번 세션 테스트 수: {len(session_records)}
- 누적 테스트 수: {len(history)}
- 스코어링 가중치: CAGR {W_CAGR*100:.0f}% / MDD {W_MDD*100:.0f}% / 샤프 {W_SHARPE*100:.0f}% / 칼마 {W_CALMAR*100:.0f}%

---

## 이번 세션 Top 포트폴리오

| 순위 | ID | ETF 구성 | CAGR | MDD | 샤프 | 리밸런싱 | 스코어 |
|-----|----|----------|------|-----|------|---------|--------|
{session_rows}

---

## 누적 전체 Top 포트폴리오

| 순위 | ID | ETF 구성 | CAGR | MDD | 샤프 | 리밸런싱 | 스코어 |
|-----|----|----------|------|-----|------|---------|--------|
{all_rows}

---

## Insights (핵심 발견)

{insights_text}

---

## 다음 세션 탐색 방향 제안

- [ ] 상위 포트폴리오의 ETF 비중 변형 탐색 (exploitation)
- [ ] 2차전지, 헬스케어, 리츠(REITs) 등 추가 테마 탐색
- [ ] 채권 비중을 10%~50% 범위로 세분화한 변형 탐색
- [ ] 해외 채권 ETF (미국채, 하이일드) 혼합 전략 탐색
- [ ] 배당 ETF 위주 인컴형 포트폴리오 탐색

## 스코어링 가중치 현황

현재: CAGR {W_CAGR*100:.0f}% / MDD {W_MDD*100:.0f}% / 샤프 {W_SHARPE*100:.0f}% / 칼마 {W_CALMAR*100:.0f}%  
비고: 세션 1은 초기 기준선 수집이므로 가중치 변경 유보. 세션 2부터 조정 검토.
"""
    return md


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="연금 ETF 포트폴리오 Agent 백테스트")
    parser.add_argument("--turns", type=int, default=100, help="이번 세션 테스트 수 (기본: 100)")
    parser.add_argument("--session", type=int, default=None, help="세션 번호 (기본: 자동)")
    args = parser.parse_args()

    # 기존 기록 로드
    history = load_history()
    session = args.session
    if session is None:
        session = (max((r["session"] for r in history), default=0)) + (1 if history else 1)
        # 마지막 세션이 완료됐는지 확인
        if history:
            last_session = max(r["session"] for r in history)
            last_session_count = sum(1 for r in history if r["session"] == last_session)
            if last_session_count < 100:
                session = last_session  # 이어서 진행

    existing_ids = {r["id"] for r in history}
    global_turn = len(history) + 1

    # 사용할 포트폴리오 목록
    candidates = CANDIDATE_PORTFOLIOS[: args.turns]
    if len(candidates) < args.turns:
        # 부족한 경우 랜덤 변형 추가 (현재는 사전 정의만 사용)
        candidates = candidates

    print(f"=== 연금 ETF Agent 백테스트 시작 ===")
    print(f"세션: {session}, 예정 턴 수: {len(candidates)}")
    print(f"기존 누적 기록: {len(history)}개")
    print()

    # ETF 이름 매핑 로드
    with open(ETF_LIST_PATH, "r", encoding="utf-8") as f:
        etf_list_data = json.load(f)
    code_to_name = {e["code"]: e["name"] for e in etf_list_data}

    # 가격 데이터 사전 로드
    print("ETF 가격 데이터 로드 중...")
    get_etf_prices()
    print(f"로드 완료: {len(get_etf_prices().columns)}개 ETF\n")

    session_records = []

    for turn_in_session, (name, code_weights, rebalance, rebal_freq, notes) in enumerate(candidates, 1):
        turn_id = f"test_{global_turn:04d}"
        print(f"[턴 {turn_in_session}/{len(candidates)}] {turn_id}: {name}")

        record = run_single_backtest(
            code_weight_list=code_weights,
            rebalance=rebalance,
            rebalance_freq=rebal_freq,
            turn_id=turn_id,
            session=session,
            turn=turn_in_session,
            notes=notes,
            code_to_name=code_to_name,
        )

        if record is None:
            print(f"  → 스킵됨\n")
            continue

        history.append(record)
        session_records.append(record)

        # 스코어 재계산 (전체 기록 기반 백분위수)
        history = compute_scores(history)
        # session_records 내 score 도 갱신
        record_ids = {r["id"] for r in session_records}
        session_records = [r for r in history if r["id"] in record_ids]

        # 매 턴마다 저장
        save_history(history)

        m = record["metrics"]
        print(
            f"  → CAGR: {m['cagr_pct']:.2f}%  MDD: {m['mdd_pct']:.2f}%  "
            f"샤프: {m.get('sharpe_ratio') or 'N/A'}  스코어: {record['score']:.1f}  "
            f"기간: {record['backtest']['start_date']} ~ {record['backtest']['end_date']}\n"
        )

        global_turn += 1

    # 세션 완료: insights MD 생성
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    insights_path = BASE_DIR / f"insights_{date_str}_S{session}.md"
    insights_md = generate_insights_md(history, session, session_records, date_str)
    with open(insights_path, "w", encoding="utf-8") as f:
        f.write(insights_md)

    print(f"=== 세션 {session} 완료 ===")
    print(f"  테스트 완료: {len(session_records)}개 / 예정 {len(candidates)}개")
    print(f"  누적 기록: {len(history)}개")
    print(f"  결과 저장: {HISTORY_PATH}")
    print(f"  인사이트: {insights_path}")

    # 상위 3개 출력
    top3 = sorted(session_records, key=lambda r: r["score"], reverse=True)[:3]
    print("\n▶ 이번 세션 Top 3:")
    for i, r in enumerate(top3, 1):
        etf_str = ", ".join(f"{e['name']}({e['weight']*100:.0f}%)" for e in r["portfolio"]["etfs"])
        print(f"  {i}. [{r['score']:.1f}점] {etf_str}")
        print(f"     CAGR {r['metrics']['cagr_pct']:.2f}% / MDD {r['metrics']['mdd_pct']:.2f}%")


if __name__ == "__main__":
    main()
