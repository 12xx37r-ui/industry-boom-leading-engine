# Industry Boom Leading Engine v0.4.1

GitHub Actions에서 데이터 수집·계산·과거 재현·JSON 생성을 수행하고, Google Apps Script는 결과만 표시합니다.

## V0.4.1 핵심 변경

기존 버전은 계약·매출이 이미 커진 산업을 높게 평가해, **이미 붐이 진행 중인 산업**과 **붐 이전에 자금이 축적되는 산업**을 한 점수로 섞었습니다.  
V0.4.1은 이를 분리합니다.

### 1. 초기 선행점수

다음을 중심으로 계산합니다.

- 기술연구 확산 가속도
- 현금흐름표 실제 CAPEX
- 시설투자 금액
- 초기 매출 수요
- 참여기업 확산도

### 2. 상업화 실현점수

다음을 별도로 계산합니다.

- 공급계약·수주금액
- 시설투자 공시금액
- 매출 성장
- 영업이익률 개선

### 3. 실적 대중화 전 선행격차

초기 선행점수는 강하지만 상업화 실현점수가 아직 낮을 때, 본격적인 붐 이전의 `EARLY_ACCUMULATION` 단계로 판정합니다.  
기술연구만 높고 CAPEX·매출·확산이 따라오지 않으면 교차확인 점수에서 감점합니다.

### 4. 새 단계

- `EARLY_ACCUMULATION`: 초기 자금축적
- `TRANSITION`: 상업화 전환
- `COMMERCIAL_BOOM`: 본격 상업화
- `WATCH`: 관찰
- `NO_SIGNAL`: 신호 없음
- `INSUFFICIENT_DATA`: 자료 부족

### 5. 실행시간 개선

- 수동 검증 실행에서는 `run_replay=true`로 2022년 AI 재현시험까지 수행합니다.
- 평일 자동 실행은 과거 재현을 생략하고, 저장소에 커밋된 기존 재현결과를 재사용합니다.
- 첫 전체 실행은 OpenDART 원문·현재 재무·과거 재현을 모두 수집하므로 오래 걸릴 수 있습니다.
- 이후 실행은 `.cache`를 복원해 더 빨라집니다.

## 필요한 Secrets

- `OPENDART_API_KEY` — 필수
- `FRED_API_KEY` — 선택 보조, 기존 키 그대로 사용
- `BEA_API_KEY` — 선택 보조

`SEC_USER_AGENT`는 `use_sec=true`일 때만 사용합니다.

## 첫 검증 실행

Actions → **Industry Boom Engine** → **Run workflow**

- `as_of`: 비움
- `replay_as_of`: `2022-10-31`
- `use_sec`: `false`
- `run_replay`: `true`

AI 재현이 끝난 뒤 일상적인 현재 순위 갱신만 할 때는 `run_replay=false`를 사용하면 됩니다.

## 주요 출력

- `industry_boom_ranking.json`
- `industry_boom_detail.json`
- `ai_replay_2022.json`
- `model_validation.json`
- `event_amount_quality.json`
- `cashflow_capex_quality.json`
- `technology_momentum.json`
- `macro_context.json`
- `bea_context.json`
- `engine_health.json`

## V0.4 점수 필드

- `boom_score`: 붐 이전 선행기회 점수
- `early_signal_score`: 기술·CAPEX·초기수요 선행점수
- `commercial_realization_score`: 계약·매출·마진 상업화 점수
- `cross_confirmation_score`: 연구·CAPEX·매출·확산 교차확인
- `transition_gap_score`: 상업화가 대중화되기 전의 선행격차
- `prediction_score_6m`
- `prediction_score_12m`
- `prediction_score_24m`

## 검증 원칙

2022년 AI 재현시험은 다음을 확인합니다.

- 선행기회 순위 3위 이내
- 선행기회 점수 60점 이상
- 초기 선행점수 60점 이상
- 단계가 `EARLY_ACCUMULATION` 또는 `TRANSITION`
- 투자·계약금액 추출률 35% 이상
- 독립 기술연구 데이터 존재

이 기준을 통과해도 성공·실패 산업 전체 워크포워드 백테스트가 끝나기 전에는 `investment_use_allowed=false`를 유지합니다.


## V0.4.1 hotfix
- BEA Secret에 복사 잔여문자나 제로폭 문자가 포함돼도 36자 UUID를 자동 추출합니다.
- BEA 키 형식이 비정상이어도 보조 데이터원 오류로 기록하고 핵심 엔진 실행은 계속합니다.
