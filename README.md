# Industry Boom Leading Engine V3.2.0

GitHub Actions 전용 기업투자 계층입니다.

## 새 데이터

- 미국 Census 2023 AIES 산업별 CAPEX
- NSF NCSES BERD 산업별 기업 R&D 2008~2023
- 기존 V3.1 연구·정부지출·고용·사업체·임금 신호

공식 XLSX 원본 2개를 저장소 안에도 시점 고정 seed로 포함합니다. 라이브 다운로드가 실패하면 해당 공식 seed를 사용합니다. SEC·FMP·BAT·CMD·Colab은 사용하지 않습니다.

## 실행

Actions에서 `00 - Industry Boom V3.2 Corporate Investment`만 실행합니다. `run_date`는 일반 실행 시 비워둡니다.

생성 Artifact: `industry-boom-v3.2-corporate-investment-result`

`phase3_investment_signal_score`는 최종 `boom_score`가 아닙니다. 매출 전환·공급망 확산·미래 성과 검증 전까지 투자 사용은 금지됩니다.
