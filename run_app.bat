@echo off
REM ============================================================================
REM  X-Metrics 3.0 - Risk Planner. Sobe o app Streamlit da worktree release/3.0.
REM  Cria a .venv e instala as dependencias na primeira execucao.
REM
REM  Usa requirements-lock.txt (versoes pinadas, identicas a venv 2.0 que
REM  funciona) em vez de requirements.txt: a resolucao do requirements.txt puxa
REM  versoes novas que dependem de pyiceberg, cujo build falha no Python 3.14.
REM ============================================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [X-Metrics 3.0] Criando ambiente virtual .venv...
  python -m venv .venv
)

REM (Re)instala as deps se o streamlit nao importar.
".venv\Scripts\python.exe" -c "import streamlit" >nul 2>&1
if errorlevel 1 (
  echo [X-Metrics 3.0] Instalando dependencias ^(primeira execucao^)...
  ".venv\Scripts\python.exe" -m pip install -q -r requirements-lock.txt
)

if not exist "Env\Topstep_bi.env" (
  echo [X-Metrics 3.0] AVISO: Env\Topstep_bi.env ausente.
  echo                 Copie de "E:\BD\260502 BI TopStep\Env" antes de logar.
)

echo [X-Metrics 3.0] Iniciando app em http://localhost:8501 ...
".venv\Scripts\python.exe" -m streamlit run src\app.py

endlocal
