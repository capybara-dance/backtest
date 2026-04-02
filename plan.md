# 연금 계좌 ETF 포트폴리오 최적화 Agent 계획서

> 최초 작성: 2026-04-02  
> 작성자: AI Agent  
> 상태: 초안

---

## 1. 목표

한국 연금 계좌(IRP/연금저축)에서 운용 가능한 **최적 ETF 포트폴리오**를 자동으로 탐색한다.

- 후보: `etf_list.json` 의 국내상장 ETF 1,084개
- 포트폴리오 크기: 최대 10개 ETF
- 운용 방식: **거치식(Lump-Sum)**
- 평가 지표: 수익률(CAGR), MDD, 샤프지수, 변동성 등을 종합 스코어링
- 100턴씩 전략을 구성·테스트하고, 결과를 누적 기록하여 다음 Agent가 이어받는다

---

## 2. 파일 구조

```
backtest/
├── etf_list.json          # 국내상장 ETF 1,084개 (code, name)
├── test_history.json      # 누적 테스트 기록 (JSON Lines 또는 배열)
├── backtest_logic.py      # 백테스트 핵심 로직 (기존)
├── plan.md                # 이 파일 - 전체 계획 및 Agent 역할
├── agent.md               # 이전 Agent 작업 로그
└── insights_YYYYMMDD.md   # 매 100턴 세션 종료 후 생성되는 분석 리포트
```

---

## 3. Agent 역할 및 워크플로우

### 3-1. 세션 시작 시 (필수 준비)

Agent는 작업을 시작하기 전 반드시 다음을 수행한다.

1. **`test_history.json` 읽기** → 과거 테스트된 포트폴리오, 스코어, insight 파악
2. **최신 `insights_*.md` 읽기** → 이전 Agent가 남긴 분석 결과 흡수
3. **탐색 방향 결정** → 기록된 결과를 바탕으로 탐색 우선순위 설정:
   - 고스코어 포트폴리오의 변형 탐색 (exploitation)
   - 아직 테스트되지 않은 자산군/섹터 탐색 (exploration)

### 3-2. 1 턴 = 1 전략 테스트

```
[1] ETF 선정 (최대 10개)
    - 탐색 전략: 랜덤 / 섹터 기반 / 이전 고스코어 변형 / 특정 테마
    - yfinance로 수익률 데이터 존재 여부 확인 후 확정

[2] 백테스트 실행 (backtest_logic.py 활용)
    - 기간: 가능한 최대 기간 (데이터가 존재하는 모든 기간)
    - 방식: 거치식 (Lump-Sum)
    - 리밸런싱: 미적용 / 월별 / 분기별 / 연간 각각 테스트 (또는 Agent가 1개 선택)
    - 배당 재투자: 적용

[3] 성과 지표 계산
    - CAGR (연평균 수익률)
    - MDD (최대 낙폭)
    - 샤프지수 (무위험이자율: 연 3% 가정)
    - 변동성 (연간화 표준편차)
    - Calmar Ratio (CAGR / |MDD|)
    - 총 수익률

[4] 종합 스코어 계산 (0~100점)
    점수 = w1 * score(CAGR) + w2 * score(-MDD) + w3 * score(Sharpe) + w4 * score(Calmar)
    - 기본 가중치: w1=0.35, w2=0.30, w3=0.20, w4=0.15
    - 각 지표는 전체 test_history 대비 백분위수로 정규화

[5] test_history.json 저장 (항목 추가)

[6] 다음 턴으로
```

### 3-3. 100턴 종료 시

1. 해당 세션의 상위 10개 포트폴리오 선정
2. 섹터/자산군 분석 → 고스코어 포트폴리오의 공통 특성 파악
3. **`insights_YYYYMMDD_N.md`** 생성 (누적 세션 번호 포함)
4. 다음 세션을 위한 탐색 방향 제안 기술

---

## 4. `test_history.json` 스키마

```json
[
  {
    "id": "test_0001",
    "session": 1,
    "turn": 1,
    "triggered_by": "ai_agent",
    "timestamp": "2026-04-02T03:00:00Z",
    "portfolio": {
      "etfs": [
        {"code": "069500", "name": "KODEX 200", "weight": 0.4},
        {"code": "360750", "name": "TIGER 미국S&P500", "weight": 0.3},
        {"code": "114260", "name": "KODEX 국채10년", "weight": 0.3}
      ],
      "rebalancing": {
        "enabled": false,
        "frequency": null
      },
      "reinvest_dividends": true
    },
    "backtest": {
      "start_date": "2017-01-02",
      "end_date": "2026-03-31",
      "invest_type": "거치식",
      "initial_amount": 10000000
    },
    "metrics": {
      "cagr_pct": 12.34,
      "total_return_pct": 165.2,
      "mdd_pct": -28.5,
      "sharpe_ratio": 0.87,
      "volatility_annual_pct": 14.2,
      "calmar_ratio": 0.43,
      "years": 9.25
    },
    "score": 72.4,
    "notes": "S&P500 + 국내주식 + 채권 기본 혼합 전략"
  }
]
```

### 필드 설명

| 필드 | 설명 |
|------|------|
| `id` | 고유 식별자 (`test_NNNN`) |
| `session` | 세션 번호 (100턴 = 1세션) |
| `turn` | 세션 내 턴 번호 (1~100) |
| `triggered_by` | 트리거 주체: `"ai_agent"`, `"user"`, `"scheduled"` |
| `timestamp` | UTC 기준 ISO 8601 |
| `portfolio.etfs` | 선정 ETF 목록 (code, name, weight) |
| `portfolio.rebalancing` | 리밸런싱 설정 |
| `backtest.start_date` | 실제 데이터 시작일 |
| `backtest.end_date` | 실제 데이터 종료일 |
| `metrics` | 성과 지표 |
| `score` | 종합 스코어 (0~100) |
| `notes` | 전략 설명 및 특이사항 |

---

## 5. 스코어링 공식

### 5-1. 각 지표 정규화

전체 `test_history` 의 분포를 기준으로 백분위수(0~100) 변환:

```
score_cagr    = percentile_rank(cagr_pct)         # 높을수록 좋음
score_mdd     = percentile_rank(-mdd_pct)          # 절대값 작을수록 좋음 (낙폭 적음)
score_sharpe  = percentile_rank(sharpe_ratio)      # 높을수록 좋음
score_calmar  = percentile_rank(calmar_ratio)      # 높을수록 좋음
```

### 5-2. 종합 스코어

```
score = 0.35 * score_cagr
      + 0.30 * score_mdd
      + 0.20 * score_sharpe
      + 0.15 * score_calmar
```

> 가중치는 insights MD에 기록된 Agent 분석에 의해 조정될 수 있음

---

## 6. 탐색 전략 (Exploration Strategy)

### Phase 1: 기반 구축 (세션 1 — 첫 100턴)

다양한 자산 배분 전략을 넓게 탐색한다.

| 탐색 유형 | 비율 | 예시 |
|----------|------|------|
| 섹터 혼합 (국내+해외+채권) | 40% | KODEX200 + TIGER미국S&P500 + 국채 |
| 단일 자산 집중 | 20% | 미국 기술주 ETF 위주 |
| 주식+채권 혼합 (60/40 변형) | 20% | 주식60%+채권40% 다양한 비율 |
| 테마/섹터 특화 | 20% | 반도체, 2차전지, 헬스케어 등 |

### Phase 2 이후 (세션 2+)

- 이전 세션 상위 20% 포트폴리오의 변형 탐색 (exploitation): 60%
- 신규 탐색 (exploration): 40%

---

## 7. insights MD 형식

세션 종료 시 `insights_YYYYMMDD_S<N>.md` 로 저장.

```markdown
# 연금 ETF 포트폴리오 분석 리포트

## 세션 정보
- 세션 번호: N
- 테스트 기간: YYYY-MM-DD ~ YYYY-MM-DD
- 이번 세션 테스트 수: 100
- 누적 테스트 수: NNN

## 이번 세션 Top 10 포트폴리오

| 순위 | ID | ETF 구성 | CAGR | MDD | 샤프 | 스코어 |
|-----|----|----------|------|-----|------|--------|
| 1 | test_XXXX | ... | ...% | ...% | ... | 87.3 |
...

## 누적 전체 Top 10

...

## Insights (핵심 발견)

1. [발견사항 1]
2. [발견사항 2]
...

## 다음 세션 탐색 방향 제안

- [ ] [탐색 방향 1]
- [ ] [탐색 방향 2]
...

## 스코어링 가중치 조정 제안

현재: CAGR 35% / MDD 30% / 샤프 20% / 칼마 15%
제안: ...
이유: ...
```

---

## 8. 구현 체크리스트

### 이번 Agent가 해야 할 작업

- [ ] `test_history.json` 초기화 (없는 경우)
- [ ] ETF 데이터 가용성 사전 확인 (yfinance로 코드+`.KS` 형식 시세 조회 가능 여부)
- [ ] 백테스트 실행 스크립트 구현 (`run_agent_backtest.py`)
- [ ] 스코어링 함수 구현
- [ ] 100턴 루프 실행
- [ ] insights MD 생성 및 저장

### 다음 Agent를 위한 필수 인수인계

- `test_history.json` 에 모든 테스트 기록 저장
- 최신 `insights_*.md` 에 핵심 발견 기록
- 탐색하지 못한 자산군/전략 명시

---

## 9. 기술적 고려사항

### ETF 심볼 변환 규칙

`etf_list.json` 의 `code` 는 KRX 6자리 코드. yfinance에서 한국 ETF 조회 시:

```
yfinance 심볼 = code + ".KS"
예: "069500" → "069500.KS"
```

### 데이터 가용성 제약

- 상장 이력이 짧은 ETF는 백테스트 기간이 제한됨
- 최소 3년 이상 데이터가 있는 ETF만 포트폴리오에 포함 권장
- 동일 포트폴리오(같은 ETF 조합)의 중복 테스트 방지

### 연금 계좌 제약 반영 (실제 운용 고려사항)

- 주식형 ETF 비율 규제: IRP는 위험자산 70% 한도
- 레버리지/인버스 ETF 제외
- 국내 상장 ETF만 사용 (이미 etf_list.json으로 필터링됨)

---

## 10. 연속성 보장 원칙

> **모든 Agent는 다음 원칙을 반드시 준수한다**

1. **세션 시작 전** `test_history.json` 과 최신 `insights_*.md` 를 반드시 읽는다
2. **매 턴 완료 후** 즉시 `test_history.json` 에 결과를 추가한다 (중간 손실 방지)
3. **insights** 는 누적되며, 이전 Agent의 발견을 명시적으로 참조·인용한다
4. **탐색 방향** 은 이전 insights의 제안을 최우선으로 반영한다
5. **스코어링 공식** 변경 시 변경 이유와 날짜를 insights에 반드시 기록한다
