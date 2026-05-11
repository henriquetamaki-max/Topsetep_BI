@echo off
REM Importa todos os CSVs de "CSV input/" para o Supabase e move processados
REM para "CSV output/" renomeados como YYYYMMDD_N.csv.
REM Requer .venv criado e Env/Topstep_bi.env com SUPABASE_SERVICE_ROLE_KEY.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ERRO: .venv nao encontrado. Rode primeiro:
    echo     python -m venv .venv
    echo     .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo === Importando CSVs para Supabase ===
".venv\Scripts\python.exe" ingest.py
set EXITCODE=%ERRORLEVEL%

echo.
if %EXITCODE%==0 (
    echo === OK ===
) else (
    echo === Houve erros - veja acima ===
)
pause
exit /b %EXITCODE%
