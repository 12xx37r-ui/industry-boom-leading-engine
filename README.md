# Industry Boom Leading Engine v0.6.2

산업에 이미 돈이 몰린 뒤 따라가는 것이 아니라, **기술연구 확산 → 실제 CAPEX → 시설투자·공급계약 → 초기 매출 확산**의 순서를 분석해 향후 산업 붐 가능성을 선행 탐지하는 연구용 엔진입니다.

- 계산·수집·백테스트: GitHub Actions / Python
- 표시: Google Apps Script
- 현재 상태: 연구검증 단계, 자동 매수 금지

## V0.6.2 핵심 변경

V0.4.1의 AI 단일 재현시험을 다음과 같은 **다중 성공·음성 통제군 워크포워드 검증**으로 확장했습니다.

### 성공사례 후보

- AI 연산·데이터센터: 2022-10-31
- 기업 클라우드: 2018-12-31
- 전기차·배터리: 2019-12-31
- 태양광·전력변환: 2019-06-30

### 음성 통제군

- 메타버스·XR: 2020-12-31
- 수소·연료전지: 2020-12-31
- 3D프린팅·적층제조: 2019-12-31

각 시나리오는 목표 산업과 7개 비교산업을 동일 기준일에 평가합니다. 성공사례는 선행경보를 냈는지, 음성 통제군은 강한 허위경보를 내지 않았는지 검증합니다.

## 새 출력

- `walkforward_backtest.json`: 전체 시나리오와 세부 결과
- `backtest_summary.json`: 성공 재현율·허위경보율·균형점수
- `model_validation.json`: Stage 2 판정
- `outputs/backtests/*.json`: 시나리오별 원자료·순위·근거

## Stage 2 합격 기준

- 유효 성공사례 3개 이상
- 유효 음성 통제군 2개 이상
- 성공사례 재현율 66.7% 이상
- 음성 통제군 허위경보율 33.3% 이하
- 균형점수 0.67 이상

Stage 2를 통과해도 `investment_use_allowed`는 `false`입니다. 다음 항목이 남아 있기 때문입니다.

- 미국 빅테크·산업 원천 CAPEX
- 실물신호 대비 주가·뉴스 선반영도
- 거래비용·보유기간·최대낙폭
- 사전 정의되지 않은 신규 산업 자동발견

## 필요한 GitHub Secrets

저장소의 `Settings → Secrets and variables → Actions`에 등록합니다.

```text
FRED_API_KEY
BEA_API_KEY
OPENDART_API_KEY
```

Repository Variable:

```text
SEC_USER_AGENT
```

SEC는 선택 보강자료이며 GitHub 공용 IP에서 403이 발생할 수 있습니다.

## 1. 평소 현재 산업 순위 실행

GitHub:

```text
Actions → Industry Boom Engine → Run workflow
```

권장 입력:

```text
as_of: 비움
replay_as_of: 2022-10-31
use_sec: false
run_replay: false
```

`run_replay=false`이면 과거 검증을 반복하지 않아 실행시간을 줄입니다.

## 2. 다중 역사검증 실행

GitHub:

```text
Actions → Industry Boom Validation → Run workflow
```

추가 입력은 없습니다. 7개 시나리오가 최대 2개씩 병렬 실행됩니다. 완료 후 다음 Artifact를 다운로드합니다.

```text
industry-boom-validation-output
```

ZIP 전체를 검토용으로 사용하면 됩니다.

## 워크플로 파일

```text
.github/workflows/run_engine.yml
.github/workflows/run_validation.yml
```

웹 업로드에서 `.github` 숨김 폴더가 빠지면 GitHub의 `Add file → Create new file`에서 위 경로를 통째로 입력해 생성합니다.

## 점수 구조

### 초기 선행점수

- 기술연구 확산
- 현금흐름표 실제 CAPEX
- 시설투자 공시금액
- 초기 매출 증가
- 참여기업 확산

### 상업화 실현점수

- 공급계약·수주
- 시설투자 집행
- 매출 확장
- 영업이익률 개선

### 주요 단계

- `EARLY_ACCUMULATION`: 연구·CAPEX가 강하지만 상업화 전
- `TRANSITION`: 선행신호가 계약·매출로 전환
- `COMMERCIAL_BOOM`: 상업화가 본격화
- `WATCH`: 관찰
- `NO_SIGNAL`: 의미 있는 신호 없음
- `INSUFFICIENT_DATA`: 자료 부족

## 로컬 테스트

```bash
python -m pip install -r requirements.txt
pip install -e .
pytest -q
```

## 개별 시나리오 실행

```bash
python -m ible.backtest_cli --scenario-id AI_2022
python -m ible.backtest_cli --scenario-id CLOUD_2018
```

## 검증결과 집계

```bash
python -m ible.aggregate_backtests \
  --input-dir outputs/backtests \
  --output-dir outputs
```

## 주의

- 역사적 성공·실패 라벨은 연구용 벤치마크이며 완전한 인과적 정답표가 아닙니다.
- OpenDART 정정공시와 arXiv 과거 집계는 완전한 point-in-time 빈티지를 보장하지 못할 수 있습니다.
- 현재 출력은 산업 조사 우선순위를 정하는 용도이며 매수 명령이 아닙니다.

## V0.6.2 재점수 입력 안정화

`validation_seed/backtests`에 이미 수집된 7개 역사 시나리오 원자료를 포함한다. 따라서
`Industry Boom Validation Rescore`는 이전 GitHub Artifact가 삭제되거나 API에서 보이지 않아도
외부 API 재수집 없이 재점수 계산을 완료한다. 이 폴더는 실행에 필요한 검증 입력이며 임시 산출물이 아니다.

---

# V0.8.0 — 노출도 기반 글로벌 SEC 엔진

V0.7 홀드아웃 실패 원인이었던 `관련 기업 전체 실적을 테마 실적으로 귀속`하는 구조를 제거했습니다.

## 핵심 변경

- SEC 공식 nightly `companyfacts.zip` 벌크 아카이브를 1회 다운로드
- 분석 대상 CIK JSON만 추출해 GitHub Cache에 저장
- 기업별 테마 노출도 × 근거 신뢰도의 유효가중치 적용
- 노출도 30% 미만 기업은 점수 계산 제외
- 한 기업 의존도가 45%를 넘으면 집중도 감점
- 기술연구만 급증하고 CAPEX·매출이 확인되지 않는 경우 hype penalty 적용
- OpenDART 한국 기업은 이번 글로벌 홀드아웃에서 제외하고 미국 원천기업만 검증

## 실행

Actions → `Industry Boom Global Holdout V0.8` → Run workflow

첫 실행은 SEC 벌크 ZIP 다운로드 때문에 시간이 걸릴 수 있습니다. 이후 실행은 `.cache/sec_bulk/subset` 캐시를 사용합니다.

완료 Artifact: `industry-boom-global-holdout-v0.8.0`
