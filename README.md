# Industry Boom Leading Engine v0.2.0

산업의 현재 인기가 아니라 **실제 투자·계약금액, 매출·이익, 기술연구 확산이 지속·가속되는지**를 계산해 향후 산업 붐 후보를 순위화하는 GitHub Actions 엔진입니다. Google Apps Script는 계산하지 않고 `outputs/*.json`만 표시합니다.

## V0.2.0 핵심 변경

- OpenDART 공시 **건수 중심 계산을 폐기**하고 공시 원문에서 투자금액·계약금액을 추출
- 기업 규모 왜곡을 줄이기 위해 추출금액을 최근 4개 분기 매출로 정규화
- 금액 추출 실패 시 건수 신호를 제한적으로 폴백하며 추출률을 별도 공개
- 키 없이 쓰는 arXiv 기술연구 확산 신호 추가
- FRED API 우선 사용, GitHub 403 발생 시 공식 FRED CSV 자동 우회
- BEA 요청 메서드·URL 변형 자동 교정 및 FixedAssets 카탈로그 확인
- AI 재현 검증에 순위·점수 외 금액추출률·독립연구신호 기준 추가
- 모든 오류·JSON의 API 키 자동 마스킹 유지

## 필요한 GitHub Secrets

```text
OPENDART_API_KEY   # 필수
FRED_API_KEY       # 권장, 기존 키 그대로 사용
BEA_API_KEY        # 권장
```

선택 Repository Variable:

```text
SEC_USER_AGENT
```

SEC는 GitHub 호스팅 러너에서 403이 발생할 수 있어 기본적으로 사용하지 않습니다.

## 실행

Actions → **Industry Boom Engine** → **Run workflow**

```text
as_of: 비워두기
replay_as_of: 2022-10-31
use_sec: false
```

로그의 주요 구간:

```text
[DART] original-document amount extraction targets=...
[DART] amount extraction ...
[ARXIV] technology momentum ...
[FRED] macro context with official CSV fallback
[BEA] dataset/catalog check
```

## 주요 출력

```text
outputs/industry_boom_ranking.json
outputs/industry_boom_detail.json
outputs/ai_replay_2022.json
outputs/technology_momentum.json
outputs/event_amount_quality.json
outputs/macro_context.json
outputs/bea_context.json
outputs/model_validation.json
outputs/engine_health.json
```

`model_validation.json`의 `investment_use_allowed`는 V0.2.0에서도 의도적으로 `false`입니다. AI 재현 1단계가 통과해도 성공·실패 산업 전체 워크포워드 백테스트를 통과하기 전에는 실전 투자판정에 사용하지 않습니다.

## Google Apps Script

`google_apps_script/Code.gs`, `Index.html`, `appsscript.json`을 Apps Script 프로젝트에 넣고 Script Properties에 다음을 설정합니다.

```text
OUTPUT_BASE_URL = GitHub Raw outputs 폴더 URL
```

예: 저장소의 `outputs` 폴더가 Raw로 읽히는 기본 URL이며, 끝에 `/`는 넣지 않습니다.

## 로컬 테스트

```bash
python -m pip install -r requirements.txt
pip install -e .
pytest -q
```
