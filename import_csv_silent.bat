@echo off
REM Versao silenciosa de import_csv.bat para uso com o Task Scheduler do Windows.
REM Diferenca: sem "pause", redireciona output para logs\ingest-YYYYMMDD.log e
REM preserva o exit code do ingest.py (Task Scheduler usa para retry/alertas).

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [%date% %time%] ERRO: .venv nao encontrado.
    exit /b 1
)

if not exist "logs" mkdir logs

REM Nome do log = data BR (YYYYMMDD via wmic; locale-independent).
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set TODAY=%%i
set LOGFILE=logs\ingest-%TODAY%.log

echo. >> "%LOGFILE%"
echo === [%date% %time%] inicio === >> "%LOGFILE%"
".venv\Scripts\python.exe" ingest.py >> "%LOGFILE%" 2>&1
set EXITCODE=%ERRORLEVEL%
echo === [%date% %time%] fim (exit=%EXITCODE%) === >> "%LOGFILE%"

exit /b %EXITCODE%
