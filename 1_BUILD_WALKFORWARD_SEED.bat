@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title Industry Boom V1.0.0 Independent Walkforward Seed Builder

echo ================================================================
echo  Industry Boom V1.0.0 - Independent Walkforward Seed Builder
echo  SEC and arXiv data are collected once on this PC.
echo  GitHub Actions will use only the checked-in seed files.
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
    echo Python 3 is not installed. Trying automatic installation...
    where winget >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Python and winget were not found.
        echo Install Python 3.12 from python.org, then run this file again.
        pause
        exit /b 2
    )
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo [ERROR] Automatic Python installation failed.
        pause
        exit /b 2
    )
    if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
        set "PYEXE=%LocalAppData%\Programs\Python\Python312\python.exe"
        set "PYARGS="
    ) else (
        echo [ERROR] Python was installed but is not visible in this window.
        echo Close this window and run the BAT again.
        pause
        exit /b 2
    )
)

echo Python command: %PYEXE% %PYARGS%
echo.
set "SEC_EMAIL="
set /p "SEC_EMAIL=Enter your SEC contact email: "
if not defined SEC_EMAIL (
    echo [ERROR] Email is required.
    pause
    exit /b 2
)

echo.
echo Building two independent point-in-time seeds: 2019-04-30 and 2019-10-31.
echo Existing SEC ZIP files in local_sec_data will be reused.
echo If SEC blocks one ZIP, download the exact URL shown in the error,
echo save it into local_sec_data with the same file name, and run this BAT again.
echo.

"%PYEXE%" %PYARGS% "tools\build_walkforward_seed_local.py" --email "%SEC_EMAIL%"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [ERROR] Walkforward seed build failed. Exit code: %RC%
    echo Read the final WALKFORWARD-SEED-ERROR line above.
    pause
    exit /b %RC%
)

echo.
echo ================================================================
echo  COMPLETE
echo  Upload this folder to the TOP LEVEL of the GitHub repository:
echo  UPLOAD_THIS_FOLDER_TO_GITHUB\validation_seed\walkforward
echo ================================================================
if exist "%CD%\UPLOAD_THIS_FOLDER_TO_GITHUB" start "" explorer.exe "%CD%\UPLOAD_THIS_FOLDER_TO_GITHUB"
pause
exit /b 0
