# Industry Boom Leading Engine V0.1

GitHub Actions에서 공식 공개데이터를 수집·계산하고 JSON을 생성하는 산업 붐 선행예측 엔진의 첫 실행본입니다.
Google Apps Script는 후속 단계에서 이 저장소의 `outputs/*.json`을 읽어 표시만 하도록 연결합니다.

## V0.1이 실제로 하는 일

- SEC Company Facts를 기업별로 수집합니다.
- 공시일(`filed`)이 기준일 이후인 자료를 제거해 과거시점 재현 오류를 줄입니다.
- 산업 테마별 CAPEX, R&D, 매출, 매출총이익의 분기 흐름을 계산합니다.
- 수준·증가속도·가속도·지속성·기업 확산도를 계산합니다.
- 현재 산업 후보 순위와 2022-10-31 AI 과거 재현 순위를 동시에 생성합니다.
- FRED는 기준일 빈티지 방식으로 거시·제조업 문맥을 생성합니다.
- OpenDART는 한국 수혜기업의 시설투자·공급계약 공시를 보강자료로 수집합니다.
- BEA 키 연결상태와 사용 가능한 데이터셋을 검사합니다.

## 중요한 한계

V0.1은 **사전 정의된 산업 후보군을 순위화**합니다. 세상에 존재하는 모든 신규 산업을 텍스트에서 자동으로 발견하는 완전한 블라인드 탐색기는 아직 아닙니다.
`ai_replay_2022.json`도 당시 공개된 수치만 사용하지만 산업 분류 자체는 현재 관점으로 구성한 `taxonomy-aware replay`입니다.

따라서 V0.1의 목적은 다음 세 가지입니다.

1. API 연결과 point-in-time 계산이 정상인지 검증
2. AI가 2022-10-31 기준 상위권에 올라오는지 확인
3. 성공·실패 테마 백테스트를 위한 데이터 기반 확립

## 필요한 GitHub Secrets

- `FRED_API_KEY`
- `BEA_API_KEY`
- `OPENDART_API_KEY`

Repository Variable:

- `SEC_USER_AGENT` — 예: `IndustryBoomLeadingEngine/0.1 your-email@example.com`

API 키는 코드나 채팅에 붙여넣지 마십시오.

## GitHub Actions 실행

워크플로: **Industry Boom Engine**

기본 입력:

- `as_of`: 비우면 실행일
- `replay_as_of`: `2022-10-31`
- `include_dart`: `true`

성공하면 Artifact `industry-boom-engine-output`과 저장소 `outputs/`에 JSON이 생성됩니다.

## 출력 파일

- `industry_boom_ranking.json`: 현재 산업 순위
- `industry_boom_detail.json`: 산업별 상세점수
- `ai_replay_2022.json`: AI 과거 재현시험
- `macro_context.json`: FRED 빈티지 거시 문맥
- `korea_corroboration.json`: 한국 시설투자·공급계약 보강
- `engine_health.json`: 데이터원 연결·한계
- `run_manifest.json`: 실행 요약

## 로컬 실행

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
pip install -e .
pytest -q
python -m ible.cli --as-of 2026-08-01 --replay-as-of 2022-10-31 --include-dart
```

## 다음 버전의 합격 과제

- BEA 산업별 고정자산·투자 데이터를 산업 점수에 직접 편입
- 성공·실패 산업의 워크포워드 백테스트와 확률 보정
- 산업 테마를 미리 지정하지 않는 SEC 공시 키워드·특허·채용 기반 자동발견
- 주가 미반영도와 기존 기업가치 엔진 연결
- Google Apps Script 대시보드 연결

## Google Apps Script 연결

`google_apps_script/`의 세 파일을 Apps Script 프로젝트에 복사합니다.

Script Properties에 다음 값을 추가합니다.

- 이름: `OUTPUT_BASE_URL`
- 값: `https://raw.githubusercontent.com/본인계정/industry-boom-leading-engine/main/outputs`

그다음 웹 앱으로 배포합니다. 저장소가 비공개이면 Raw URL을 직접 읽을 수 없으므로 공개 출력 전용 저장소 또는 인증 프록시가 필요합니다.
