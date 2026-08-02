# 산업 붐 선행예측 엔진 V4.0 FINAL AUTO

## 현재 완료 상태

- 엔진 구축: **96%**
- 전체 프로젝트: **85%**
- 산업 후보군: **50개**
- 직접 사업화 관측: **48개 산업**
- 최종 남은 관문: 새 예측을 6·12·24개월 뒤 실제 성과로 검증

## 실행할 것은 하나뿐입니다

GitHub Actions에서 다음 워크플로만 최초 한 번 실행합니다.

**`00 - ONLY RUN THIS - Industry Boom V4.0 Final Auto`**

`run_date`는 비워 둡니다. 최초 실행 후에는 매주 월요일 자동 실행되므로 V2·V3.0·V3.1·V3.2·V3.3을 따로 실행하지 않습니다.

## 한 번에 자동 수행되는 단계

1. OpenAlex 연구 확산
2. USAspending 정부 투자
3. BLS QCEW 고용·사업체·임금
4. Census AIES 산업별 CAPEX
5. NSF BERD 기업 R&D
6. 사업화·공급망 확산 신호
7. Census QSS 서비스산업 직접 매출
8. Census M3 제조업 출하·신규수주
9. 50개 산업 사전검증 후보 순위
10. 6·12·24개월 미래성과 검증 큐 저장

## 생성 결과

Artifact 이름:

`industry-boom-v4.0-final-auto-result`

핵심 결과 파일:

- `v40_run_summary.json`
- `v40_direct_commercialization_observations.json`
- `v40_prevalidation_candidate_ranking.json`
- `v40_model_lock_verification.json`
- `v40_next_gate.json`

## 판정 제한

`prevalidation_candidate_score`는 직접 매출·출하·수주까지 반영한 사전검증 후보점수입니다. 아직 6·12·24개월 미래성과가 쌓이지 않았으므로 최종 `boom_score`와 투자 허용 판정은 활성화하지 않습니다. 과거 결과를 보고 V0.9.1 계산식을 수정하지 않습니다.
