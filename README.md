# 산업 붐 선행예측 엔진 V1.1.0 — 블라인드 산업·시점 홀드아웃

## 이번 단계

V0.9.1 모델 계산식은 그대로 잠그고, 기존 역사 시나리오에서 **당시 개발 목표가 아니었던 다른 산업·날짜 조합 8개**를 별도 봉인해 검증합니다.

- 성공 산업 4개
- 과열·정체 통제군 4개
- 모델·판정식 수정 없음
- BAT·CMD·Colab 없음
- SEC·FMP·외부 네트워크 호출 없음
- GitHub Actions 한 번으로 테스트·검증·Artifact 생성
- 업로드 파일 100개 미만

## 정확한 검증 지위

이번 결과는 `sealed_blind_theme_date_holdout_not_external`입니다.

기존 개발 목표와 다른 산업을 평가하므로 V1.0.4보다 강한 블라인드 검증이지만, 원천 스냅샷은 기존 번들과 같으므로 **신규 외부 데이터 독립검증으로 허위 표시하지 않습니다.** 결과가 통과해도 투자 사용은 계속 금지됩니다.

## 실행

1. ZIP 압축을 풉니다.
2. 내용물 전체를 기존 GitHub 저장소에 덮어씁니다.
3. Actions에서 **Industry Boom V1.1 Blind Theme Holdout**만 선택합니다.
4. `Run workflow`를 실행합니다.
5. 완료 후 `industry-boom-v1.1-blind-holdout-result` Artifact를 받습니다.

## 결과 파일

- `v1_1_blind_holdout_summary.json`
- `v1_1_blind_holdout_ranking.json`
- `v1_1_blind_holdout_scenarios.json`
- `v1_1_model_lock_verification.json`
- `v1_1_next_gate.json`
