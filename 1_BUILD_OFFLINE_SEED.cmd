@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title Industry Boom V0.8.10 Offline Seed Builder

echo ================================================================
echo  Industry Boom V0.8.10 - Offline Seed Builder
echo  This runs on your PC. GitHub will not call SEC or FMP.
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
    if not errorlevel 1 (
        set "PYEXE=python"
    )
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
        echo Install Python 3.12 manually, then run this file again.
        pause
        exit /b 2
    )

    if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
        set "PYEXE=%LocalAppData%\Programs\Python\Python312\python.exe"
        set "PYARGS="
    ) else (
        where py >nul 2>nul
        if not errorlevel 1 (
            set "PYEXE=py"
            set "PYARGS=-3"
        ) else (
            echo [ERROR] Python was installed but cannot be found in this window.
            echo Close this window and run this BAT file again.
            pause
            exit /b 2
        )
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
echo Downloading official SEC quarterly files and building the offline seed.
echo Existing downloads will be reused if this process is restarted.
echo.

"%PYEXE%" %PYARGS% "tools\build_offline_seed_local.py" --email "%SEC_EMAIL%"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [ERROR] Offline seed build failed. Exit code: %RC%
    echo Check the last OFFLINE-SEED-ERROR message above.
    echo If an SEC ZIP is blocked, download the shown URL in your browser,
    echo save it into the local_sec_data folder with the same file name,
    echo and run this BAT file again.
    pause
    exit /b %RC%
)

echo.
echo ================================================================
echo  COMPLETE
echo  Upload this folder to the TOP LEVEL of your GitHub repository:
echo  UPLOAD_THIS_FOLDER_TO_GITHUB\validation_seed
echo ================================================================

if exist "%CD%\UPLOAD_THIS_FOLDER_TO_GITHUB" (
    start "" explorer.exe "%CD%\UPLOAD_THIS_FOLDER_TO_GITHUB"
)

pause
exit /b 0
