# 산업 붐 선행예측 엔진 V6.1

## 목적

V6.1은 새 점수를 다시 튜닝하는 버전이 아니라 운영 검증판입니다.

- V0.9.1 Champion 계산식은 계속 동결합니다.
- V6.0 연구 Challenger는 자동 승격하지 않습니다.
- 50개 산업의 월별 prevalidation 순위에 보수 정책과 확장 정책의 경보를 동시에 기록합니다.
- 같은 달의 정책 스냅샷은 수정하거나 덮어쓰지 않습니다.
- V5 결과가 6·12·24개월 만기에 도달하면 각 정책의 정밀도·재현율·오경보를 자동 비교합니다.

## 실행

GitHub Actions에서 다음 워크플로 하나만 실행합니다.

`00 - RUN THIS ONLY - Industry Boom V6.1.1 Prospective Ledger`

`run_date`는 일반 실행 시 비워둡니다. 최초 실행 이후 매주 월요일 자동 실행됩니다.

## 현재 판정

- 기능 개발: 완료
- 50개 산업 데이터 파이프라인: 운영 중
- 역사 검증: Champion 재현율 부족, Challenger 연구 성적 개선
- 미래 독립검증: 아직 6개월 만기 전
- 투자 사용: 금지

## 중요 제한

V6.1 live policy는 V5의 `prevalidation_candidate_score`에 적용하는 별도 미래검증 정책입니다. V0.9.1의 `boom_score`와 같은 점수라고 표시하지 않으며 모든 `boom_score`는 `null`로 유지합니다.

## GitHub 업로드 제한

릴리즈 실제 파일 수와 manifest 파일 수를 모두 검사하며 90개 미만만 허용합니다. BAT·CMD·Colab·SEC·FMP는 사용하지 않습니다.
