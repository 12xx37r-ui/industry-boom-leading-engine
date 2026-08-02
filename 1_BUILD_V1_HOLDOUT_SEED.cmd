@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title Industry Boom V1.0 Holdout Seed Builder

echo ================================================================
echo  Industry Boom V1.0 - Offline Holdout Seed Builder
echo  SEC and arXiv data are collected ON THIS PC only.
echo  GitHub Actions will calculate from the finished JSON with no API calls.
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
    echo [ERROR] Python 3 is required.
    echo Install Python 3.12 from python.org and run this file again.
    pause
    exit /b 2
)

set "SEC_EMAIL="
set /p "SEC_EMAIL=Enter your SEC contact email: "
if not defined SEC_EMAIL (
    echo [ERROR] Email is required by SEC fair-access policy.
    pause
    exit /b 2
)

echo.
echo [1/2] Building the locked AI 2022 replay seed...
"%PYEXE%" %PYARGS% "tools\build_offline_seed_local.py" --email "%SEC_EMAIL%" --request-file "config/v1_offline_seed_request.json" --output-file "validation_seed/sec_fsds_v1_ai_2022.json"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto :FAILED

echo.
echo [2/2] Building the independent 2023H1 external holdout seed...
"%PYEXE%" %PYARGS% "tools\build_offline_seed_local.py" --email "%SEC_EMAIL%" --request-file "config/v1_external_seed_request.json" --output-file "validation_seed/sec_fsds_v1_external_2023h1.json"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto :FAILED

echo.
echo ================================================================
echo  COMPLETE
echo  Upload BOTH generated JSON files inside:
echo  UPLOAD_THIS_FOLDER_TO_GITHUB\validation_seed
echo ================================================================
if exist "%CD%\UPLOAD_THIS_FOLDER_TO_GITHUB" start "" explorer.exe "%CD%\UPLOAD_THIS_FOLDER_TO_GITHUB"
pause
exit /b 0

:FAILED
echo.
echo [ERROR] V1 seed build failed. Exit code: %RC%
pause
exit /b %RC%
