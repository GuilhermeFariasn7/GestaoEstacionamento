@echo off
title Smart Parking - Diagnostico do Gemini
cd /d "%~dp0"
if not exist "venv\Scripts\activate.bat" (
    echo [ERRO] Ambiente virtual nao encontrado.
    pause
    exit /b 1
)
call "venv\Scripts\activate.bat"
python diagnostico_gemini.py %*
echo.
pause
