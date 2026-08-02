# 산업 붐 선행예측 엔진 V6.2 — GAS Dashboard + Prospective Ledger

V6.1의 Champion–Challenger 월별 불변 예측을 유지하면서, GitHub에 커밋된 최신 결과 JSON을 읽는 Google Apps Script 대시보드를 추가한 통합 릴리즈입니다.

## GitHub 실행

1. ZIP의 내용물 전체를 기존 저장소에 덮어씁니다.
2. Actions에서 **`00 - RUN THIS ONLY - Industry Boom V6.2 Dashboard + Ledger`**만 실행합니다.
3. 이후 매주 월요일 자동 실행되며 결과 JSON이 저장소 `outputs/`와 `prospective_history/`에 커밋됩니다.

## GAS 대시보드 배포

`google_apps_script/` 폴더의 다음 3개 파일을 하나의 Apps Script 프로젝트에 넣습니다.

- `Code.gs`
- `Index.html`
- `appsscript.json`

### Script Properties

Apps Script 왼쪽 **프로젝트 설정 → 스크립트 속성**에서 등록합니다.

공개 GitHub 저장소:

- `GITHUB_OWNER`: GitHub 사용자명
- `GITHUB_REPO`: 저장소명, 기본값 `industry-boom-leading-engine`
- `GITHUB_BRANCH`: 기본값 `main`

비공개 저장소는 추가로:

- `GITHUB_TOKEN`: Fine-grained token, 해당 저장소 **Contents: Read**

대체 설정:

- `OUTPUT_BASE_URL`: `https://raw.githubusercontent.com/<owner>/<repo>/<branch>` 형태의 저장소 루트 URL

### 웹 앱 배포

1. Apps Script에서 `diagnoseDashboardConnection`을 한 번 실행해 권한을 승인합니다.
2. **배포 → 새 배포 → 웹 앱**
3. 실행 사용자: 나
4. 액세스 권한: 모든 사용자
5. 배포 URL을 엽니다.

## 화면 구성

- 전체 진행률과 데이터 기준일
- Champion·Challenger 경보 수
- 산업 TOP20 및 섹터/경보 필터
- 사업화·기업투자·원천 확산 점수
- 월별 순위 변화 및 신규 진입
- 역사 벤치마크와 봉인 블라인드 성적
- 6·12·24개월 미래검증 성적표

## 고정 원칙

- V0.9.1 계산식 변경 금지
- Challenger 자동 승격 금지
- `boom_score` 임의 생성 금지
- BAT·CMD·Colab·SEC·FMP 사용 금지
- GitHub 업로드 파일 90개 미만
