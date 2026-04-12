@echo off
:: ============================================================
:: run.bat -- Windows command runner (replaces make commands)
:: Usage: run.bat <command>
::
:: Commands:
::   run.bat backtest          Walk-forward backtest (HTML report in reports\)
::   run.bat paper             Paper trading bot (TWS on port 7497)
::   run.bat live              Live trading bot (requires confirmation)
::   run.bat dashboard         Web dashboard at http://localhost:5000
::   run.bat diagnose          Analyze paper trade log, show findings
::   run.bat diagnose-html     Diagnostic + save HTML report to reports\
::   run.bat retrain           Manually retrain ML meta-labeler
::   run.bat test              Run pytest test suite
::   run.bat scan              Scan live signals without placing orders
::   run.bat check-config      Validate .env and config.yaml
::   run.bat sync-params       List QuantConnect parameter variants
::   run.bat clean             Remove Python cache files
::   run.bat help              Show this help
:: ============================================================

if "%1"=="" goto :help
if /i "%1"=="help" goto :help

if /i "%1"=="backtest"      goto :backtest
if /i "%1"=="paper"         goto :paper
if /i "%1"=="live"          goto :live
if /i "%1"=="dashboard"     goto :dashboard
if /i "%1"=="diagnose"      goto :diagnose
if /i "%1"=="diagnose-html" goto :diagnose_html
if /i "%1"=="retrain"       goto :retrain
if /i "%1"=="test"          goto :test
if /i "%1"=="scan"          goto :scan
if /i "%1"=="check-config"  goto :check_config
if /i "%1"=="sync-params"   goto :sync_params
if /i "%1"=="clean"         goto :clean

echo  Unknown command: %1
echo  Run "run.bat help" to see available commands.
goto :end

:backtest
echo  Running walk-forward backtest...
python scripts\run_backtest.py %2 %3 %4 %5
goto :end

:paper
echo  Starting paper trading bot...
python scripts\run_live.py --paper
goto :end

:live
echo.
echo  WARNING: This will place REAL orders with REAL money.
echo  Make sure TRADING_MODE=live is set in .env
echo.
set /p CONFIRM="Type YES to continue (anything else cancels): "
if /i "%CONFIRM%"=="YES" (
    python scripts\run_live.py --live
) else (
    echo  Cancelled.
)
goto :end

:dashboard
echo  Starting dashboard at http://localhost:5000
echo  Press Ctrl+C to stop.
python scripts\run_dashboard.py
goto :end

:diagnose
echo  Analyzing paper trade log...
python scripts\diagnose_paper.py
goto :end

:diagnose_html
echo  Analyzing paper trade log + saving HTML report...
python scripts\diagnose_paper.py --html
goto :end

:retrain
echo  Retraining ML meta-labeler...
python scripts\run_retrain.py
goto :end

:test
echo  Running test suite...
python -m pytest tests\ -v --cov=src --cov-report=term-missing
goto :end

:scan
echo  Scanning current signals (no orders placed)...
python scripts\scan_signals.py
goto :end

:check_config
echo  Validating configuration...
python -c "from src.config import settings; settings.validate(); print('Config OK')"
goto :end

:sync_params
echo  QuantConnect Parameter Variants:
python scripts\sync_qc_params.py --list
goto :end

:clean
echo  Cleaning Python cache files...
for /d /r . %%d in (__pycache__) do (
    if exist "%%d" rd /s /q "%%d" 2>nul
)
del /s /q *.pyc >nul 2>&1
echo  Done.
goto :end

:help
echo.
echo  Trading Bot -- Windows Command Runner
echo  ======================================
echo.
echo  Usage: run.bat ^<command^>
echo.
echo  TRADING
echo    backtest         Walk-forward backtest (HTML report saved to reports\)
echo    paper            Start paper trading bot (connect TWS on port 7497)
echo    live             Start live trading (requires TRADING_MODE=live in .env)
echo    scan             Scan live signals without placing any orders
echo.
echo  MONITORING
echo    dashboard        Web dashboard at http://localhost:5000
echo    diagnose         Analyze paper trade log, print findings to terminal
echo    diagnose-html    Same as diagnose + save HTML report to reports\
echo.
echo  MAINTENANCE
echo    retrain          Manually retrain ML meta-labeler
echo    check-config     Validate .env and config.yaml settings
echo    sync-params      List QuantConnect parameter variants
echo    test             Run pytest test suite with coverage
echo    clean            Remove Python __pycache__ and .pyc files
echo    help             Show this message
echo.
echo  FIRST TIME SETUP
echo    Run setup.bat to install dependencies and create .env
echo.

:end
