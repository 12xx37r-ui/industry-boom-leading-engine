# 산업 붐 선행예측 엔진 V7.0 완성 운영판

이 릴리즈는 다음 네 가지 미연결 항목을 실제 엔진 필드로 채웁니다.

- 대중 관심도: GDELT 글로벌 뉴스 + Wikimedia 페이지뷰 상대점수
- V7 운영 boom_score: 실물·사업화 통합신호 80% + 대중 관심도 20%
- Hidden Opportunity Score: 실물신호 75% + 낮은 대중관심 25%
- 최근 3개월 변화: 3개월 잠금 이력 우선, 없으면 90일 원천 모멘텀 프록시
- 기업 브리지: 50개 산업 모두 대표 상장기업 후보 매핑

V0.9.1 Champion은 변경하지 않습니다. V7 점수는 미래 6·12·24개월 검증 전 투자등급 점수가 아닙니다.

## 실행
Actions에서 `00 - RUN THIS ONLY - Industry Boom V7.0 Complete Engine`만 실행합니다.


## V7.0.5 패키지 분리

이 ZIP은 **Python/GitHub Actions 엔진 전용**입니다. Google Apps Script 파일은 별도 `industry-boom-gas-v7.0.4.zip`으로 배포합니다. 엔진 저장소에는 `google_apps_script` 폴더를 넣지 않습니다.

## V7.0.5 receipt rollover fix

V6.0 비교 영수증은 봉인된 V5.1 증거와 정책을 검증합니다. 워크플로 실행일은 봉인 증거의 변경이 아니므로, 기존 영수증의 `evidence_as_of`를 보존해 날짜가 바뀌어도 재사용합니다. 정책·모델·판정 내용이 달라지면 기존처럼 즉시 중단합니다.


## V7.4 public-interest migration (NAVER primary + resilient GDELT)

The operational public-interest layer now uses NAVER DataLab Search Trend as the primary signal.
Set these GitHub Actions repository secrets before the live run:

- `NAVER_API_HUB_CLIENT_ID`
- `NAVER_API_HUB_CLIENT_SECRET`

`config/v3_naver_interest.json` contains the 50-theme Korean keyword dictionary and a common anchor group used to make NAVER ratio batches cross-comparable. Wikimedia remains in the repository for compatibility/diagnostics but is disabled from the live public-interest score.

GDELT DOC 2.0 remains enabled as a global-news fallback. GDELT requests now use a dedicated single-attempt HTTP client plus globally serialized source-level retries, preventing concurrent 429 retry bursts. The service can still throttle GitHub-hosted traffic; stale cache is retained when available.
