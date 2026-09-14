@echo off
setlocal EnableExtensions
title Smart Parking - Testes do TCC

cd /d "%~dp0"
cls

set "APP_URL=http://127.0.0.1:5000"
set "TESTS_URL=%APP_URL%/tests"
set "HEALTH_URL=%APP_URL%/health"
set "SERVER_TITLE=Smart Parking - Servidor de Testes"

echo ============================================================
echo              SMART PARKING - TESTES DO TCC
echo ============================================================
echo.
echo Esta pagina e destinada somente aos experimentos do TCC.
echo Ela e independente do fluxo do usuario final.
echo.

REM ------------------------------------------------------------
REM [1/3] Verifica se o servidor ja esta rodando
REM ------------------------------------------------------------
echo [1/3] Verificando se o servidor esta em execucao...
echo.
call :CheckServer
if not errorlevel 1 goto OPEN_TESTS

REM ------------------------------------------------------------
REM [2/3] Inicia o servidor
REM ------------------------------------------------------------
echo [2/3] Servidor nao esta em execucao. Iniciando...
echo.

if not exist "venv\Scripts\activate.bat" (
    echo [ERRO] Ambiente virtual nao encontrado em "venv\Scripts\activate.bat".
    echo Crie o venv com:  python -m venv venv
    echo.
    pause
    exit /b 1
)

if not exist "app.py" (
    echo [ERRO] Arquivo "app.py" nao encontrado em "%~dp0".
    echo.
    pause
    exit /b 1
)

REM Abre o servidor em uma janela separada, minimizada.
REM SMART_PARKING_OPEN_PAGE=tests avisa o app.py qual pagina abrir.
start "%SERVER_TITLE%" /min cmd /k "cd /d "%~dp0" && call "venv\Scripts\activate.bat" && set "SMART_PARKING_OPEN_PAGE=tests" && python app.py"

echo Aguardando o servidor iniciar (ate 15s)...
call :WaitServer 15
if errorlevel 1 (
    echo.
    echo [ERRO] O servidor nao iniciou corretamente em 15 segundos.
    echo Verifique a janela "%SERVER_TITLE%" para detalhes do erro.
    echo.
    pause
    exit /b 1
)

REM ------------------------------------------------------------
REM [3/3] Tudo pronto
REM ------------------------------------------------------------
echo.
echo [3/3] Servidor OK.

:OPEN_TESTS
echo.
echo Servidor OK.
echo Abrindo %TESTS_URL% ...
echo.

REM O app.py ja abre o navegador quando SMART_PARKING_OPEN_PAGE=tests.
REM Por isso NAO usamos "start" aqui para nao abrir duas abas.
REM Se o seu app.py NAO abrir automaticamente, descomente a linha abaixo:
REM start "" "%TESTS_URL%"

endlocal
exit /b 0

REM ============================================================
REM Sub-rotinas
REM ============================================================

:CheckServer
REM Retorna errorlevel 0 se o servidor estiver respondendo, 1 caso contrario.
powershell -NoProfile -Command "try { $r=Invoke-WebRequest -Uri '%HEALTH_URL%' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>&1
exit /b %errorlevel%

:WaitServer
REM Uso: call :WaitServer <segundos>
REM Tenta por N segundos; retorna 0 se subiu, 1 se estourou o tempo.
setlocal
set /a "_tries=%~1"
for /L %%i in (1,1,%_tries%) do (
    powershell -NoProfile -Command "try { $r=Invoke-WebRequest -Uri '%HEALTH_URL%' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>&1
    if not errorlevel 1 (
        endlocal & exit /b 0
    )
    timeout /t 1 /nobreak >nul
)
endlocal & exit /b 1