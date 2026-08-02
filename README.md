# 산업 붐 선행예측 엔진 V6.0 — Champion–Challenger

## 이번 버전의 목적

V5.1 감사에서 확인된 문제는 **오경보율 0%를 유지했지만 성공산업 재현율이 33.33%에 그쳤다는 것**입니다.

V6.0은 기존 V0.9.1을 수정하지 않습니다.

- **Champion:** V0.9.1 완전 동결·현행 유지
- **Challenger:** `WATCH` 단계 중 정량 확인이 강한 사례만 추가 경보
- **사용 상태:** 연구용 Shadow only
- **자동 승격:** 금지
- **투자 사용:** 금지

## Challenger 정책

기존 Champion 경보는 모두 보존합니다. 추가 경보는 아래 조건을 전부 만족할 때만 발생합니다.

- 단계: `WATCH`
- 순위: 4위 이내
- Boom score: 58.0 이상
- Early signal score: 57.5 이상
- 데이터 신뢰도: 60 이상

이 정책은 V5.1 진단 뒤 만들어졌으므로 같은 역사자료에서 성적이 좋아져도 외부 독립검증으로 간주하지 않습니다.

## 실행

GitHub Actions에서 아래 워크플로만 실행합니다.

`00 - RUN THIS ONLY - Industry Boom V6.0 Champion Challenger`

`run_date`는 일반 실행 시 비워둡니다.

## 예상 연구 비교 결과

- 역사 벤치마크 Champion 재현율: 33.33%
- 역사 벤치마크 Challenger 재현율: 66.67%
- 봉인 홀드아웃 Champion 재현율: 75%
- 봉인 홀드아웃 Challenger 재현율: 100%
- Challenger 오경보율: 0%

단, 동일 증거 재사용이므로 **Champion은 계속 현행 모델로 남고 Challenger는 미래 Shadow 검증만 진행**합니다.

## 다음 관문

Challenger를 새 월별 시점자료에 동시에 기록하고 6·12·24개월 실제 성과를 Champion과 비교합니다.
