# 산업 붐 선행예측 엔진 V1.0.4 — GitHub 오류 방지본

이번 버전은 기존 GitHub 저장소에 과거 `tests/test_fmp.py`, `tests/test_sec_fsds.py`, `tests/test_arxiv.py` 등이 남아 있어도 실행하지 않도록 수정했습니다.

## 발생했던 오류

기존 Actions가 `python -m pytest`로 저장소 전체 테스트를 수집하면서 폐기된 SEC·FMP·arXiv 코드까지 불러왔고, `requests`와 `yaml` 모듈 누락 오류가 발생했습니다.

## 근본 수정

- `pyproject.toml`에서 테스트 대상을 `tests/test_release.py` 하나로 고정
- Actions에서도 `tests/test_release.py`만 명시적으로 실행
- 과거 테스트 파일이 저장소에 남아 있어도 수집하지 않음
- BAT·CMD·Colab 없음
- SEC·FMP 네트워크 호출 없음
- GitHub 내부 압축 검증팩만 사용
- 배포 파일은 100개 미만

## 업로드

1. ZIP 압축을 풉니다.
2. 내용물 전체를 기존 GitHub 저장소에 덮어씁니다.
3. GitHub `Actions`에서 **Industry Boom V1.0.4 GitHub Robust Validation**을 실행합니다.
4. 완료 후 `industry-boom-v1.0.4-github-result` Artifact를 받습니다.

기존의 과거 테스트 파일을 일일이 삭제하지 않아도 됩니다. 새 설정이 해당 파일들을 실행 대상에서 제외합니다.

## 결과

- `v1_github_validation_summary.json`
- `v1_github_validation_ranking.json`
- `v1_model_lock_verification.json`

현재 7개 사례는 V0.9.1 역사 잠금 사례이므로 신규 독립 외부 홀드아웃 통과로 표시하지 않습니다. `independent_external_holdout.status`는 `NOT_RUN`으로 유지됩니다.
