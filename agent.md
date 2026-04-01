# 국내상장 ETF 목록 수집 작업 과정

## 목표

[capybara_fetcher](https://github.com/capybara-dance/capybara_fetcher) 의 릴리즈 파일로부터 국내상장 ETF 목록을 추출하여 JSON으로 저장한다.

---

## 작업 과정

### 1. capybara_fetcher 레포지토리 파악

- 레포지토리: https://github.com/capybara-dance/capybara_fetcher
- 릴리즈 파일 구성(README.md 참조):
  1. `korea_universe_feature_frame.parquet` — 전체 종목 Feature 데이터
  2. `korea_universe_feature_frame.meta.json` — 메타데이터
  3. `korea_industry_feature_frame.parquet` — 업종 Feature 데이터
  4. `korea_industry_feature_frame.meta.json` — 업종 메타데이터
  5. **`krx_stock_master.parquet`** — KRX 종목 마스터 (KOSPI/KOSDAQ/ETF 포함)

### 2. 최신 릴리즈 확인

- GitHub API로 릴리즈 목록 조회
- 최신 릴리즈 태그: `data-20260401-1759` (2026-04-01 발행)

### 3. krx_stock_master.parquet 다운로드

다음 URL에서 파일 다운로드:

```
https://github.com/capybara-dance/capybara_fetcher/releases/download/data-20260401-1759/krx_stock_master.parquet
```

### 4. 데이터 분석

`krx_stock_master.parquet` 파일 구조:

| 컬럼명 | 설명 |
|--------|------|
| `Code` | 종목코드 (6자리) |
| `Name` | 종목명 |
| `Market` | 시장구분 (`KOSPI`, `KOSDAQ`, `ETF`) |
| `IndustryLarge` | 대분류 업종 (ETF는 null) |
| `IndustryMid` | 중분류 업종 (ETF는 null) |
| `IndustrySmall` | 소분류 업종 (ETF는 null) |
| `SharesOutstanding` | 상장주식수 (ETF는 null) |

- 전체 종목 수: **3,760개**
- 시장별 분류:
  - KOSDAQ: 1,827개
  - **ETF: 1,084개**
  - KOSPI: 849개

### 5. ETF 필터링 및 JSON 저장

`Market == 'ETF'` 조건으로 필터링 후 `etf_list.json`으로 저장.

저장 형식:
```json
[
  {
    "code": "069500",
    "name": "KODEX 200",
    "market": "ETF"
  },
  ...
]
```

---

## 결과

- 추출된 ETF 수: **1,084개**
- 저장 파일: `etf_list.json`
- 데이터 출처: `capybara_fetcher` 릴리즈 `data-20260401-1759` 의 `krx_stock_master.parquet`
