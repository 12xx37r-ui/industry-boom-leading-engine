# Industry Boom Leading Engine v0.6.0

산업에 이미 돈이 몰린 뒤 따라가는 것이 아니라, **기술연구 확산 → 실제 CAPEX → 시설투자·공급계약 → 초기 매출 확산**의 순서를 분석해 향후 산업 붐 가능성을 선행 탐지하는 연구용 엔진입니다.

- 계산·수집·백테스트: GitHub Actions / Python
- 표시: Google Apps Script
- 현재 상태: 연구검증 단계, 자동 매수 금지

## V0.6.0 핵심 변경

V0.5.0 검증에서 AI는 통과했지만 클라우드와 전기차·배터리는 놓쳤습니다. 원인은 하나의 선행공식이 모든 산업을 같은 방식으로 평가한 데 있었습니다.

V0.6.0은 산업 붐의 선행경로를 두 가지로 분리합니다.

### 연구주도 경로

- 논문·기술확산
- 연구개발
- 초기 CAPEX
- 매출 수요
- 기업 확산

AI·클라우드처럼 연구와 기술확산이 먼저 가속되는 산업에 적용됩니다.

### 실물투자주도 경로

- 현금흐름표 실제 CAPEX
- 생산능력 확대
- 초기 매출 수요
- 공급망 확산
- 기술확산 보조확인

전기차·배터리·전력망처럼 생산설비 투자가 먼저 증가하는 산업에 적용됩니다.

두 경로 중 강한 값을 선행점수로 사용하지만, 연구·CAPEX·매출·기업확산의 교차확인 기준은 유지합니다. 새 단계 `CAPITAL_LED_ACCUMULATION`을 추가했습니다.

같은 V0.5.0 원자료를 재계산하면:

- AI: 연구주도 초기축적
- 클라우드: 교차확인된 초기축적
- 전기차·배터리: 실물투자주도 초기축적
- 메타버스·수소·3D프린팅: 강한 선행경보 없음
- 태양광: 과거 기업자료 부족으로 판정 제외

이는 기존 검증자료를 이용한 구조 보정이므로 투자 사용은 계속 금지합니다. 다음 단계는 새로운 홀드아웃 산업·시점으로 재검증하는 것입니다.

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
