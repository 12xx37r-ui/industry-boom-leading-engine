# 산업 붐 선행예측 엔진 V1.0.2 — GitHub 전용 실행본

이번 버전은 로컬 실행 단계를 완전히 제거했습니다.

## 제거한 것

- BAT·CMD 파일
- Colab 노트북
- PC 로컬 경로
- SEC 자동 다운로드
- SEC API 및 분기 ZIP 호출
- FMP 호출
- 100개가 넘는 업로드 구조

## 실행 구조

```text
GitHub 저장소
  ├─ V0.9.1 동결 계산 파일
  ├─ 단일 압축 검증팩
  └─ GitHub Actions
          ↓
     잠금값 검증
          ↓
     역사 워크포워드 결과 집계
          ↓
     결과 JSON Artifact 생성
```

GitHub Actions 실행 중 외부 재무·연구 데이터를 수집하지 않습니다. 저장소에 포함된 `validation_seed/v1_locked_backtests.json.gz`만 읽습니다.

## 업로드 방법

1. 기존 저장소 파일을 삭제하거나 새 저장소를 만듭니다.
2. 이 ZIP의 **내용물 전체**를 GitHub에 업로드합니다.
3. `Actions` 메뉴로 이동합니다.
4. `Industry Boom V1.0.2 GitHub Only Validation`을 선택합니다.
5. `Run workflow`를 누릅니다.
6. 완료 후 `industry-boom-v1.0.2-github-only-result` Artifact를 받습니다.

## 결과 파일

- `v1_github_validation_summary.json`
- `v1_github_validation_ranking.json`
- `v1_model_lock_verification.json`

## 중요한 검증 구분

포함된 7개 사례는 V0.9.1 개발 당시 이미 존재하던 역사 사례입니다. 따라서 이번 실행은 **모델 잠금·재현성·GitHub 실행 경로 검증**이며, 신규 독립 외부 홀드아웃 통과로 표시하지 않습니다.

신규 점시점 외부 데이터가 없는 상태에서 기존 자료를 독립검증이라고 표시하면 검증 조작이 되므로, 결과 JSON에는 외부 독립검증 상태가 `NOT_RUN`으로 명시됩니다.

## 고정 원칙

- V0.9.1 계산식 변경 금지
- 결과 확인 후 기준값 변경 금지
- FMP 재도입 금지
- GitHub Actions에서 SEC 직접 호출 금지
- `investment_use_allowed=false` 유지
