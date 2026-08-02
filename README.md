# 산업 붐 선행예측 엔진 V2.1.0

## 이번 단계

- 후보 산업을 7개에서 **50개**로 확장
- 기존 V0.9.1 점수 7개는 그대로 유지
- 나머지 43개 산업은 **DATA_PIPELINE_PENDING**으로 분리
- 자료가 없는 산업에 추정 점수를 만들지 않음
- CAPEX·R&D·정부/기관투자·공급망·채용·연구/특허·사업화·영업생존력의 8개 필수 축 계약 추가
- GitHub Actions에서 산업 커버리지, 데이터 구축 우선순위, Shadow Ledger를 한 번에 생성

## 실행

GitHub Actions에서 **00 - Industry Boom V2.1 50-Theme Expansion**만 실행합니다. `run_date`는 비워 둡니다.

Artifact: `industry-boom-v2.1-theme-expansion-result`

## 예상 결과

- 전체 산업: 50
- 기존 점수 산출 산업: 7
- 신규 데이터 구축 대기: 43
- 임의 생성 점수: 0
- 투자 사용: 금지

다음 관문은 우선순위 1 산업부터 8개 점시점 데이터 축을 채우는 것입니다.
