# 산업 붐 선행예측 엔진 V5.1 — 역사 워크포워드 감사 + 미래 자동검증

## 이번 단계의 목적

V5.0의 월별 미래검증을 유지하면서, 기다리지 않고 과거 성능을 먼저 재감사합니다.

- 기존 역사 벤치마크: 7개 기준일
- V1.1 봉인 블라인드 테마·날짜 사례: 8개
- V0.9.1 계산식: 변경 없이 SHA-256 잠금
- 역사 seed: 개별 SHA-256 봉인
- 미래 검증: 기존 월별 불변 스냅샷과 6·12·24개월 자동평가 유지

## 중요한 사실

이 버전은 과거 결과를 좋게 보이도록 자동 통과시키지 않습니다. 기존 7개 벤치마크의 성공산업 재현율이 기준보다 낮으면 `RECALL_GAP`으로 정확히 표시합니다.

또한 기존 7개와 V1.1 8개는 신규 외부 데이터가 아니므로 `external_independence=false`, `investment_use_allowed=false`를 유지합니다.

완전한 월별 과거 빈티지 데이터가 없기 때문에 7개 기준일 사이를 임의 보간하거나 가짜 월별 결과를 만들지 않습니다.

## 실행할 워크플로 하나

GitHub Actions에서 다음만 실행합니다.

**`00 - RUN THIS ONLY - Industry Boom V5.1 Historical + Prospective Validator`**

`run_date`는 비워 둡니다. 첫 실행 이후 매주 월요일 자동 실행됩니다.

## 생성 Artifact

`industry-boom-v5.1-historical-prospective-result`

핵심 결과:

- `v51_run_summary.json`
- `v51_historical_audit.json`
- `v51_benchmark_scenarios.json`
- `v51_blind_holdout_cases.json`
- `v51_dashboard_payload.json`
- `v51_next_gate.json`
- 기존 V5.0 미래검증 결과 전체

## 금지사항 유지

- BAT/CMD/Colab 없음
- GitHub에서 SEC/FMP 호출 없음
- 결과를 보고 V0.9.1 가중치 수정 없음
- 최종 `boom_score` 임의 생성 없음
- 100개 이상 파일 배포 금지
