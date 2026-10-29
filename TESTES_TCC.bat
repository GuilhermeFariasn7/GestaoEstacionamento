@echo off
title Smart Parking - Testes do TCC
cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
    echo.
    echo [ERRO] Ambiente virtual nao encontrado em "venv\Scripts\activate.bat".
    echo.
    pause
    exit /b 1
)

call "venv\Scripts\activate.bat"
python avaliacao_gui.py
if errorlevel 1 (
    echo.
    echo [ERRO] A janela de testes fechou com erro. Leia a mensagem acima.
    pause
)
