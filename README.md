# Industry Boom Leading Engine v0.8.5

산업에 돈이 이미 몰린 뒤 따라가는 것이 아니라, 기술연구·기업 R&D·CAPEX·매출 확산을 결합해 향후 산업 붐 가능성을 선행 탐지하는 연구용 엔진입니다.

- 수집·계산·검증: GitHub Actions / Python
- 표시: Google Apps Script
- 현재 상태: 연구검증 단계
- `investment_use_allowed`: 항상 `false`

## V0.8.5 핵심 수정

V0.8.4는 FMP 무료계정으로 분기자료 20개를 요청해 `HTTP 402`가 발생했고, 이후 더 이상 지원되지 않는 legacy API까지 재시도했습니다. V0.8.5는 이 경로를 완전히 제거했습니다.

- 현재 `/stable` API만 사용
- Secret에는 `FMP_API_KEY`의 키 문자열만 저장
- 모든 요청은 `period=annual`, `limit=5`로 고정
- legacy `/api/v3` 호출 없음
- `income-statement`, `cash-flow-statement`, 통합 `financial-growth` API만 사용
- FY2021 절대값과 전년 대비 성장률로 8개 분기형 보간 시계열 생성
- 홀드아웃 기준일을 FY2021 공시가 대체로 이용 가능한 `2022-04-30`으로 변경
- 공급자 제한이나 네트워크 실패 시 워크플로를 중단하지 않고 `INSUFFICIENT_DATA` 결과와 진단 Artifact를 생성
- 재무 수집이 전부 실패하면 불필요한 arXiv 호출도 생략

연간 보간 시계열은 무료요금제 접근성 검증을 위한 임시 구조입니다. 정식 분기 시계열을 대체하지 않으며 실전 투자판정에 사용하지 않습니다.

## 필요한 GitHub 설정

Repository secret:

```text
FMP_API_KEY
```

값에는 아래처럼 키 문자열만 넣습니다.

```text
실제_API_키
```

`apikey=` 또는 `?apikey=`를 붙이지 않습니다. 코드가 URL 쿼리 파라미터로 자동 전달합니다.

Repository variable:

```text
SEC_USER_AGENT
```

이번 V0.8.5 FMP 재무수집에는 직접 사용하지 않지만 기존 엔진 호환을 위해 유지합니다.

## 실행

```text
Actions
→ Industry Boom Global Holdout V0.8.5
→ Run workflow
```

첫 실행 입력:

```text
refresh_financial_data: false
```

정상 로그 예시:

```text
IBLE_IMPORT_OK 0.8.5
FMP_API_KEY_FORMAT_OK
52 passed
[FMP-PREFLIGHT] OK working=4/4 limit=5
[FMP] companies=37 ... period=annual limit=5
```

일부 growth API가 막혀도 기본 연간 재무가 있으면 `PARTIAL`로 계속 진행합니다. 전체 접근이 막혀도 워크플로는 실패하지 않고 다음 상태로 결과를 남깁니다.

```text
INSUFFICIENT_V085_GLOBAL_HOLDOUT
```

## 결과 Artifact

```text
industry-boom-global-holdout-v0.8.5
industry-boom-financial-diagnostics-v0.8.5
```

검토할 주요 파일:

```text
outputs/global_holdout/global_holdout_summary.json
outputs/global_holdout/global_holdout_ranking.json
outputs/global_holdout/global_holdout_scenarios.json
.cache/fmp/fmp_download_status.json
```

## 검증 산업

성공 후보:

- 전력망·전기화
- 방산·무인체계
- 원전 공급망
- 사이버보안

실패·과열 통제군:

- 자율주행·라이다
- 3D프린팅
- 수소·연료전지

## 로컬 테스트

```bash
python -m pip install -r requirements.txt
python -m pytest -q
```

현재 테스트 수:

```text
52 passed
```

## 주의

- 연간 5개 제한을 우회하기 위해 실제 분기치를 조작하지 않고, FY 절대값과 공식 성장률을 분기형 경로로 보간합니다.
- 보간 여부와 공급자 오류는 결과 진단에 기록됩니다.
- 결과는 산업 연구 우선순위를 정하는 용도이며 매수·매도 명령이 아닙니다.
