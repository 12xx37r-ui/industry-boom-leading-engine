# 산업 붐 선행예측 엔진 V3.1.0

GitHub Actions 전용 실물경제 2단계입니다.

## 이번 추가

- BLS QCEW 미국 민간 산업별 분기 고용
- 사업체 수
- 총임금
- 50개 테마별 NAICS 프록시 바스켓
- 전년 동분기 비교
- 기존 V3.0.1 연구·미국 정부지출 신호와 결합한 `phase2_data_signal_score`

`phase2_data_signal_score`는 최종 산업 붐 점수가 아닙니다. 기업 CAPEX·기업 R&D·매출 전환·영업생존력 연결 전까지 `boom_score`는 `null`입니다.

## 실행

GitHub Actions에서 다음만 실행합니다.

`00 - Industry Boom V3.1 QCEW Real Economy`

일반 실행에서는 `run_date`를 비워 둡니다.

Artifact:

`industry-boom-v3.1-qcew-real-economy-result`
