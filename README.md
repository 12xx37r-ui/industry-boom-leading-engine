# 산업 붐 선행예측 엔진 V3.3 FINAL AUTO

## 현재 진척도

- 엔진 구축: **90%**
- 전체 프로젝트: **80%**
- 남은 작업: 신규 시점 예측을 6·12·24개월 뒤 실제 성과로 자동 검증하고, 직접 매출 원천을 정밀화하는 일

## 이제 실행할 것

GitHub Actions에서 **`00 - FINAL Industry Boom Auto Engine`**을 최초 한 번만 실행합니다.
그 뒤에는 기존 V3.0·V3.1·V3.2 수집 워크플로가 월요일에 순서대로 자동 갱신되고, FINAL 워크플로가 마지막에 자동 통합합니다. 사용자는 더 이상 수동 Run을 반복하지 않습니다.

## 한 번에 수행하는 단계

1. OpenAlex 연구 확산
2. USAspending 정부 지출
3. BLS QCEW 고용·사업체·임금
4. Census AIES CAPEX
5. NSF BERD 기업 R&D
6. 사업화 프록시
7. 공급망 확산 프록시
8. 시점 스냅샷과 6·12·24개월 검증 큐 저장

`phase4_readiness_signal_score`는 최종 `boom_score`가 아닙니다. 직접 기업 매출과 미래 성과 검증 전에는 투자용으로 사용할 수 없습니다.
