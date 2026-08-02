@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Industry Boom V0.8.7 Offline Seed Builder

echo ================================================================
echo  V0.8.7 SEC + arXiv 오프라인 seed 생성
echo  GitHub Actions에서 SEC/FMP를 호출하지 않도록 PC에서 1회 생성합니다.
echo ================================================================
echo.

set "PYEXE="
set "PYARGS="
where py >nul 2>nul
if not errorlevel 1 (
  set "PYEXE=py"
  set "PYARGS=-3"
) else (
  where python >nul 2>nul
  if not errorlevel 1 set "PYEXE=python"
)

if not defined PYEXE (
  where winget >nul 2>nul
  if errorlevel 1 (
    echo [실패] Python이 없고 winget도 없습니다.
    echo Python 3.12를 설치한 뒤 이 파일을 다시 실행하세요.
    pause
    exit /b 2
  )
  echo Python 3.12가 없어 자동 설치합니다...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo [실패] Python 자동 설치에 실패했습니다.
    pause
    exit /b 2
  )
  set "PYEXE=%LocalAppData%\Programs\Python\Python312\python.exe"
  set "PYARGS="
  if not exist "%PYEXE%" (
    set "PYEXE=py"
    set "PYARGS=-3"
  )
)

set "SEC_EMAIL="
set /p "SEC_EMAIL=SEC 연락용 이메일을 입력하세요: "
echo %SEC_EMAIL% | findstr /c:"@" >nul
if errorlevel 1 (
  echo [실패] 올바른 이메일 형식이 아닙니다.
  pause
  exit /b 2
)

echo.
echo 공식 SEC 분기 ZIP 6개와 과거 arXiv 건수를 수집합니다.
echo 기존 ZIP은 재사용하므로 중간에 끊겨도 다시 실행하면 됩니다.
echo.

"%PYEXE%" %PYARGS% tools\build_offline_seed_local.py --email "%SEC_EMAIL%"
if errorlevel 1 (
  echo.
  echo [실패] 위 로그의 OFFLINE-SEED-ERROR 한 줄을 확인하세요.
  echo 다운로드가 막힌 ZIP은 표시된 SEC 주소를 브라우저에서 받아
  echo local_sec_data 폴더에 같은 파일명으로 넣고 다시 실행하면 됩니다.
  pause
  exit /b 2
)

echo.
echo ================================================================
echo  완료: UPLOAD_THIS_FOLDER_TO_GITHUB\validation_seed 폴더를
echo GitHub 저장소 최상단에 업로드하세요.
echo  그 다음 Actions - Industry Boom Offline Holdout V0.8.7 실행
echo ================================================================
start "" explorer.exe "%CD%\UPLOAD_THIS_FOLDER_TO_GITHUB"
pause
exit /b 0
