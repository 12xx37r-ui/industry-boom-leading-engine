# Industry Boom Leading Engine v0.1.3

GitHub Actions에서 계산하고 Google Apps Script는 결과 JSON을 표시하는 산업 붐 선행예측 엔진입니다.

## v0.1.3 핵심 변경

GitHub 호스팅 러너의 공용 IP가 SEC `data.sec.gov`에서 HTTP 403으로 차단되는 환경에서도 실행이 중단되지 않습니다.

- 핵심 계산원: OpenDART 한국 공급망 공시·재무정보
- 보조원: FRED 과거 빈티지, BEA 연결
- SEC: 선택적 보강만 수행하며 기본값은 비활성화
- 현재 판정과 2022-10-31 AI 재현시험 모두 OpenDART 기준으로 계산

## 필요한 GitHub Secrets

- `FRED_API_KEY`
- `BEA_API_KEY`
- `OPENDART_API_KEY`

`SEC_USER_AGENT`는 SEC 보강을 켤 때만 필요합니다.

## 실행

Actions → Industry Boom Engine → Run workflow

- `as_of`: 비움
- `replay_as_of`: `2022-10-31`
- `use_sec`: `false`

## 출력

- `outputs/industry_boom_ranking.json`
- `outputs/industry_boom_detail.json`
- `outputs/ai_replay_2022.json`
- `outputs/macro_context.json`
- `outputs/korea_corroboration.json`
- `outputs/engine_health.json`
- `outputs/run_manifest.json`

## 중요 한계

현재 버전은 사전 정의된 산업군을 순위화합니다. 완전히 새로운 산업명을 자동 발견하는 단계와 미국 기업별 SEC 직접 보강은 후속 검증 단계입니다. 확률값은 투자 수익을 보장하지 않으며, 성공·실패 산업의 워크포워드 백테스트로 교정해야 합니다.
