# Industry Boom Leading Engine v0.7.0

기술연구 확산, 실제 CAPEX, 시설투자·공급계약, 초기 매출과 참여기업 확산을 결합해 향후 산업 붐의 선행 신호를 연구하는 엔진입니다.

- 수집·계산·검증: GitHub Actions / Python
- 표시: Google Apps Script
- 현재 상태: 연구검증 단계
- 자동 매수: 금지 (`investment_use_allowed=false`)

## V0.7.0 핵심: 동결모델 홀드아웃

V0.6.2에서 Stage 2 검증에 사용한 AI·클라우드·전기차·태양광·메타버스·수소·3D프린팅은 더 이상 사용하지 않습니다.
점수 공식과 판정 코드를 SHA-256으로 잠근 뒤, **새로운 8개 산업**을 2021-12-31 동일 시점과 동일 코호트에서 평가합니다.

### 새 성공 후보

- 전력망·전기화 인프라
- 방산·무인체계
- 원전·무탄소 기저전력
- 사이버보안

### 새 음성 통제군

- 자율주행·라이다 과잉기대
- NFT·블록체인 플랫폼 과열
- 대체단백질·식품테크 과잉기대
- 우주관광·민간유인비행 과잉기대

## 모델 동결

`config/model_lock.json`에 다음 파일의 해시가 저장됩니다.

```text
src/ible/analytics/scoring.py
src/ible/backtest.py
```

홀드아웃 실행 전에 두 파일이 달라지면 `MODEL_LOCK_MISMATCH`로 중단됩니다. 결과를 본 뒤 점수를 고쳐 합격시키는 것을 막기 위한 장치입니다.

## Stage 3 합격 기준

- 유효 성공사례 3개 이상
- 유효 음성 통제군 3개 이상
- 성공사례 재현율 75% 이상
- 실패테마 허위경보율 25% 이하
- 균형점수 0.75 이상
- 성공산업 점수가 실패산업보다 높은 쌍의 비율(pairwise AUC) 0.70 이상
- 모델 잠금 검증 통과

Stage 3를 통과해도 실전 투자 사용은 허용하지 않습니다. 미국 원천 CAPEX, 시장 미반영도, 거래비용과 최대낙폭 검증이 남아 있습니다.

## 필요한 Secret

이번 홀드아웃 워크플로에는 아래 하나만 필요합니다.

```text
OPENDART_API_KEY
```

`SEC_USER_AGENT`는 Repository Variable로 남아 있어도 됩니다. FRED·BEA는 이번 홀드아웃 점수 계산에 사용하지 않습니다.

## 실행 방법

저장소에 다음 파일이 있어야 합니다.

```text
.github/workflows/run_holdout_validation.yml
```

GitHub에서:

```text
Actions → Industry Boom Holdout Validation → Run workflow
```

첫 실행은 OpenDART 공시원문·재무제표·CAPEX와 arXiv 8개 산업을 수집하므로 시간이 걸릴 수 있습니다. 동일 구성으로 다시 실행하면 `.cache`를 재사용합니다.

완료 후 Artifact:

```text
industry-boom-holdout-v0.7.0
```

이 ZIP 하나만 검토용으로 사용합니다.

## 출력 파일

```text
holdout_snapshot.json
holdout_scenarios.json
holdout_summary.json
model_validation_stage3.json
model_lock_verification.json
```

## 기존 워크플로

- `run_engine.yml`: 현재 산업 순위
- `run_validation.yml`: 기존 Stage 2 역사검증
- `rescore_validation.yml`: 기존 Seed 재점수
- `run_holdout_validation.yml`: V0.7.0 신규 홀드아웃

V0.7.0 단계에서는 기존 Validation과 Rescore를 다시 실행할 필요가 없습니다.

## 로컬 테스트

```bash
python -m pip install -r requirements.txt
pip install -e .
pytest -q
```

## 주의

- 역사적 성공·실패 라벨은 연구용 벤치마크이며 완전한 인과적 정답표가 아닙니다.
- OpenDART 정정공시와 arXiv 과거 검색은 완전한 point-in-time 빈티지를 보장하지 못할 수 있습니다.
- 현재 출력은 조사 우선순위를 정하는 연구자료이며 매수 명령이 아닙니다.
