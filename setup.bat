@echo off
:: ============================================================
:: setup.bat — First-time setup for Windows (replaces make setup)
:: Usage: Double-click setup.bat OR run from Command Prompt
:: ============================================================

echo.
echo  Trading Bot -- Windows Setup
echo  =============================
echo.

:: Check Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found. Download from https://python.org
    echo  Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

:: Show Python version
for /f "tokens=*" %%i in ('python --version 2^>^&1') do echo  Found: %%i

:: Create .env from template if it doesn't exist
if not exist .env (
    if exist .env.template (
        copy .env.template .env >nul
        echo  [OK] .env created from template
        echo  IMPORTANT: Edit .env with your IBKR credentials before running the bot.
    ) else (
        echo  WARNING: .env.template not found. Create .env manually.
    )
) else (
    echo  [OK] .env already exists
)

:: Create runtime directories
if not exist data\raw        mkdir data\raw
if not exist data\processed  mkdir data\processed
if not exist logs            mkdir logs
if not exist models          mkdir models
if not exist reports         mkdir reports
if not exist quantconnect    mkdir quantconnect 2>nul
echo  [OK] Runtime directories ready

:: Upgrade pip
echo  Upgrading pip...
python -m pip install --upgrade pip --quiet

:: Install dependencies
echo  Installing dependencies (this may take 2-5 minutes)...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo  ERROR: Dependency installation failed.
    echo  Try running manually: python -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo.
echo  [OK] Setup complete!
echo.
echo  Next steps:
echo    1. Edit .env with your IBKR credentials
echo       ^> notepad .env
echo    2. Install IBKR API manually -- see README.md section "IBKR Setup"
echo    3. Run a backtest:        run.bat backtest
echo    4. Start paper trading:   run.bat paper
echo    5. Open dashboard:        run.bat dashboard
echo    6. Diagnose performance:  run.bat diagnose
echo.
pause
