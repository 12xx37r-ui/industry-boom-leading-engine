# 산업 붐 선행예측 엔진 V3.0 — GitHub Live Data Phase 1

## 이번 단계

V2.1의 43개 `DATA_PIPELINE_PENDING` 산업을 포함한 50개 산업 전체에 대해 GitHub Actions가 다음 공개 원천을 직접 조회합니다.

- OpenAlex: 글로벌 연구 문헌 건수와 1년 전 대비 변화
- USAspending: 미국 연방정부 계약·보조금·수상 건수와 1년 전 대비 변화

SEC와 FMP는 호출하지 않습니다. BAT/CMD/Colab도 없습니다. Python 외부 패키지도 설치하지 않습니다.

## 중요한 판정 원칙

이번 버전의 `phase1_data_signal_score`는 **데이터 수집 우선순위 신호**입니다. CAPEX, 기업 R&D, 채용, 매출 전환, 영업생존력 자료가 아직 없으므로 V0.9.1의 최종 `boom_score`로 변환하지 않습니다. 누락 자료를 임의 생성하지 않습니다.

## 실행

GitHub `Actions`에서 다음 하나만 실행합니다.

`00 - Industry Boom V3.0 Live Data Phase 1`

`run_date`는 일반 실행 시 비워둡니다.

## 결과 Artifact

`industry-boom-v3.0-live-data-result`

주요 결과:

- `v3_run_summary.json`
- `v3_source_observations.json`
- `v3_phase1_data_signal_ranking.json`
- `v3_data_source_health.json`
- `v3_model_lock_verification.json`
- `v3_next_gate.json`

## 실패 방지

- API 셀 하나가 실패해도 전체 실행을 즉시 중단하지 않습니다.
- 기존 캐시가 있으면 해당 셀만 `CACHE_FALLBACK`으로 유지합니다.
- 캐시도 없으면 값을 `null`로 남깁니다.
- 과거 응답은 `data_cache/YYYY/MM/DD/`에 날짜별로 저장합니다.
- 저장소 파일 수는 100개 미만으로 유지됩니다. 날짜별 캐시는 이후 누적되므로 GitHub 웹 업로드가 아니라 Actions가 자동 커밋합니다.
