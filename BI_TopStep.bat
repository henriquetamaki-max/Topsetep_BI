@echo off
REM Atalho para subir o BI TopStep e abrir no navegador.
REM Se o servidor ja estiver rodando na porta 8501, so abre o browser.

setlocal
cd /d "%~dp0"

set PORT=8501
set URL=http://localhost:%PORT%

REM Ja esta no ar? Abre o browser e sai.
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul
if %errorlevel%==0 (
    echo Streamlit ja esta rodando em %URL%. Abrindo navegador...
    start "" "%URL%"
    exit /b 0
)

REM .venv presente?
if not exist ".venv\Scripts\python.exe" (
    echo [ERRO] .venv nao encontrado. Rode:  python -m venv .venv ^&^& pip install -r requirements.txt
    pause
    exit /b 1
)

REM Sobe o Streamlit em uma janela separada (sem abrir browser duplicado).
start "BI TopStep - Streamlit" ".venv\Scripts\python.exe" -m streamlit run app.py --server.headless true --server.port %PORT%

REM Espera o servidor responder na porta antes de abrir o navegador (timeout ~30s).
echo Aguardando servidor subir em %URL% ...
for /l %%i in (1,1,60) do (
    timeout /t 1 /nobreak >nul
    netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul && goto :ready
)
echo [AVISO] Servidor demorou para subir. Abrindo navegador mesmo assim.

:ready
start "" "%URL%"
endlocal
