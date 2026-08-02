# 산업 붐 선행예측 엔진 V2.0.0 — GitHub Prospective Shadow Ledger

## 이번 단계

V1.1 블라인드 검증 통과 후, 미래 데이터로 검증하기 위한 **변경 불가능한 Shadow 기록 시스템**입니다.

- V0.9.1 계산식 SHA-256 잠금 유지
- BAT·CMD·Colab 없음
- SEC·FMP 호출 없음
- GitHub Actions 수동 실행 + 매주 월요일 자동 실행
- 동일 날짜 기록 덮어쓰기 금지
- 각 기록 SHA-256 및 이전 기록 해시 연결
- 6·12·24개월 사후채점 대기열 자동 생성
- 저장소에 `shadow_history/YYYY/MM/YYYY-MM-DD.json` 누적
- 전체 배포 파일 100개 미만

## 최초 실행의 정확한 의미

포함된 `2026-08-02` 입력은 인수인계 문서의 V0.9.1 현재 개발진단 점수를 봉인한 **시작 기준선**입니다.

따라서 최초 결과는 `V2_SHADOW_BOOTSTRAP_REGISTERED`이며 신규 외부 독립검증 성공으로 계산하지 않습니다. 이후 실제 신규 점시점 입력이 들어와야 `forecast_eligible=true`가 됩니다.

## 실행

1. ZIP 압축을 풉니다.
2. 내용물 전체를 기존 GitHub 저장소에 덮어씁니다.
3. Actions에서 **`00 - Industry Boom V2.0 Shadow Start`**를 선택합니다.
4. `Run workflow`를 누릅니다. `run_date`는 비워둡니다.
5. 완료 후 `industry-boom-v2.0-shadow-result` Artifact를 받습니다.

## 자동 보호

- 입력이 14일보다 오래되면 실패시키지 않고 `V2_SHADOW_STALE_INPUT_BLOCKED`로 종료합니다.
- 오래된 점수를 새로운 예측으로 기록하지 않습니다.
- 같은 날짜 기록이 같으면 `DUPLICATE_CONFIRMED`입니다.
- 같은 날짜 기록이 다른 내용이면 `IMMUTABILITY_VIOLATION`으로 차단합니다.

## 결과 파일

- `v2_shadow_summary.json`
- `v2_shadow_current.json`
- `v2_shadow_ledger.json`
- `v2_shadow_scorecard_queue.json`
- `v2_model_lock_verification.json`
- `v2_next_gate.json`
- `v2_persistence.json`
