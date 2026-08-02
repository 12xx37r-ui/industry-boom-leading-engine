# Industry Boom Leading Engine v0.3.0

GitHub Actions에서 데이터 수집·계산·과거 재현·JSON 생성을 수행하고, Google Apps Script는 결과만 표시합니다.

## V0.3.0 핵심 변경

- 장기 공급계약과 시설투자를 공시일 하루짜리 이벤트로 보지 않고 **실제 계약·투자기간 전체에 금액을 배분**합니다.
- OpenDART 전체 재무제표의 현금흐름표에서 **유형자산 취득액(CAPEX)** 을 직접 추출합니다.
- 기업별 CAPEX를 연매출로 나누고 4개년 수준·증가속도·가속도·확산도를 계산합니다.
- 공시 XML은 XML 파서로 처리해 기존 `XMLParsedAsHTMLWarning`을 제거합니다.
- BEA 메타데이터 요청의 `GetDatasetList` 표기를 공식 규격에 맞게 수정했습니다.
- AI 단일 산업 순위와 별도로 AI 연산 + 반도체 장비·첨단패키징 결합 진단값을 출력합니다. 결합값은 검증 합격판정에는 사용하지 않습니다.
- 비밀키 자동 마스킹과 `investment_use_allowed=false` 안전장치를 유지합니다.

## 필요한 Secrets

- `OPENDART_API_KEY` — 필수
- `FRED_API_KEY` — 선택 보조, 기존 키 그대로 사용
- `BEA_API_KEY` — 선택 보조

`SEC_USER_AGENT`는 `use_sec=true`일 때만 사용합니다.

## 실행

Actions → **Industry Boom Engine** → **Run workflow**

- `as_of`: 비움
- `replay_as_of`: `2022-10-31`
- `use_sec`: `false`

첫 실행은 OpenDART 전체 재무제표를 추가 수집하므로 이전 버전보다 호출 수가 많습니다. 이후 실행부터 GitHub 캐시를 재사용합니다.

## 주요 출력

- `industry_boom_ranking.json`
- `ai_replay_2022.json`
- `model_validation.json`
- `event_amount_quality.json`
- `cashflow_capex_quality.json`
- `technology_momentum.json`
- `macro_context.json`
- `bea_context.json`
- `engine_health.json`

## 판정 원칙

AI 과거 재현시험이 3위 이내·65점 이상 등 1단계 기준을 통과하더라도, 성공·실패 산업 전체 워크포워드 백테스트가 끝나기 전에는 투자판정에 사용하지 않습니다.
