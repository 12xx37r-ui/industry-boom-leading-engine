# 산업 붐 선행예측 엔진 V5.0 FINAL VALIDATOR

## 현재 상태

- 엔진 코드 구축: **100%**
- 전체 프로젝트 진행률: **88%**
- 산업 후보군: **50개**
- 사업화 관측 커버리지: **50개**
- 남은 단계: 새 예측의 6·12·24개월 실제 성과 누적

## 실행할 것은 하나뿐입니다

GitHub Actions에서 최초 한 번 다음 워크플로만 실행합니다.

**`00 - RUN THIS ONLY - Industry Boom V5 Final Validator`**

`run_date`는 비워 둡니다. 이후 매주 월요일 자동 실행됩니다.

첫 정상 실행 시 기존 워크플로 파일은 자동 정리되며 V5 워크플로 하나만 남도록 시도합니다.

## 이번 버전이 하는 일

1. 연구·정부투자·고용·사업체·임금 갱신
2. 산업 CAPEX·기업 R&D 갱신
3. 사업화·공급망 확산 프록시 갱신
4. QSS 서비스 매출 및 M3 제조업 출하·수주 갱신
5. 상업용 드론·우주산업의 M3 검증 프록시 보완
6. 매월 최초 실행 결과를 불변 스냅샷으로 저장
7. 각 스냅샷의 6·12·24개월 만기 도달 시 자동 성과 채점
8. 상위 후보군과 하위 후보군의 실제 성과 차이 및 순위상관 자동 계산

## 생성 Artifact

`industry-boom-v5.0-final-validator-result`

핵심 파일:

- `v50_run_summary.json`
- `v50_current_monthly_snapshot.json`
- `v50_candidate_ranking.json`
- `v50_prospective_scorecard.json`
- `v50_snapshot_registry.json`
- `v50_dashboard_payload.json`
- `v50_model_lock_verification.json`
- `v50_next_gate.json`

## 중요 판정

- V0.9.1 계산식은 계속 잠금 상태입니다.
- `prevalidation_candidate_score`는 최종 `boom_score`가 아닙니다.
- 미래 성과 관문을 통과하기 전에는 `investment_use_allowed=false`입니다.
- 동일 월 스냅샷은 덮어쓰지 않습니다.
- SEC·FMP·BAT·CMD·Colab을 사용하지 않습니다.
