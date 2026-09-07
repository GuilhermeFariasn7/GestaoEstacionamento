@echo off
title Smart Parking

cd /d "%~dp0"

cls

echo ============================================================
echo.
echo                     SMART PARKING
echo.
echo              Sistema de monitoramento
echo.
echo ============================================================
echo.

echo [1/3] Ativando ambiente virtual...

if not exist "venv\Scripts\activate.bat" (
    echo.
    echo [ERRO] Ambiente virtual nao encontrado.
    echo.
    echo Verifique se a pasta "venv" existe.
    echo.
    pause
    exit /b 1
)

call "venv\Scripts\activate.bat"

echo [OK] Ambiente virtual ativado.
echo.

echo [2/3] Verificando Python...

python --version

if errorlevel 1 (
    echo.
    echo [ERRO] Python nao esta funcionando.
    echo.
    pause
    exit /b 1
)

echo.

echo [3/3] Iniciando Smart Parking...
echo.

python app.py

echo.
echo ============================================================
echo                  SMART PARKING ENCERRADO
echo ============================================================
echo.

pause