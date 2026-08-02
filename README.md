# Industry Boom Leading Engine V0.8.7

V0.8.7은 GitHub Actions 실행 중 SEC·FMP·arXiv를 호출하지 않는 **오프라인 seed 검증 구조**입니다.

## 왜 구조를 바꿨나

GitHub 호스팅 러너에서 SEC 공식 API와 대용량 데이터셋이 403으로 차단됐고, FMP 무료 계정은 심볼·과거자료 범위가 제한됐습니다. V0.8.7은 재무·연구 원자료를 사용자 PC에서 한 번만 수집해 작은 JSON seed로 고정합니다. 이후 GitHub Actions는 외부 데이터 호출 없이 seed 무결성과 완전성을 검사하고 홀드아웃 점수만 계산합니다.

## 가장 빠른 실행 순서

1. ZIP을 Windows PC에서 압축 해제합니다.
2. `1_BUILD_OFFLINE_SEED.bat`를 더블클릭합니다.
3. SEC 연락용 이메일을 한 번 입력합니다.
4. 완료되면 생성된 `validation_seed/sec_fsds_fy2021.json`을 포함한 전체 폴더를 GitHub 저장소에 덮어씁니다.
5. `.github` 폴더가 웹 업로드에서 빠졌다면 별도 제공된 `run_global_holdout_validation_v0.8.7.yml`을 `.github/workflows/run_global_holdout_validation.yml`로 교체합니다.
6. Actions에서 `Industry Boom Offline Holdout V0.8.7`을 실행합니다.
7. Artifact `industry-boom-global-holdout-v0.8.7` 하나만 내려받습니다.

## 로컬 seed 생성 내용

- SEC Financial Statement Data Sets: `2021q1`~`2022q2`
- 기준시점: `2022-04-30`
- 대상: 노출도 게이트를 통과한 미국 기업 37개
- 항목: 매출, CAPEX, R&D, 매출총이익, 영업이익
- arXiv: 7개 산업의 최근·이전·과거 12개월 논문 건수
- 제출일과 재무기간이 기준시점 이후인 숫자는 제외

처음에는 SEC ZIP 약 6개를 받으므로 시간이 걸립니다. 다운로드된 ZIP은 `local_sec_data`에 보존되며 재실행 시 재사용됩니다.

## 실행 전 강제 게이트

GitHub Actions는 다음 조건을 모두 충족해야 점수 계산을 시작합니다.

- seed 버전·스키마 일치
- 요청 종목 37개 코호트 일치
- SEC 분기 6개 완전성
- 역사적 적격기업 대비 재무자료 확보율 75% 이상
- 사용 가능 기업 20개 이상
- arXiv 산업 7개 중 6개 이상
- seed 내용 SHA-256 무결성 일치

하나라도 실패하면 산업점수를 만들지 않고 즉시 종료합니다.

## 외부 호출

GitHub Actions 단계의 외부 데이터 API 호출은 0회입니다. `pip install`과 GitHub Action 자체 다운로드를 제외하면 검증 계산은 저장소에 포함된 seed만 사용합니다.

## 테스트

```text
60 passed
```

## 투자 사용 상태

홀드아웃 검증을 통과하더라도 `investment_use_allowed`는 계속 `false`입니다. 추가 시점·산업 워크포워드 검증 전에는 실제 투자판단에 사용하지 않습니다.
