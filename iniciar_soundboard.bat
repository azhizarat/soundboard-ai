@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title Soundboard AI [Modo Diagnostico]
cd /d "%~dp0"

echo ========================================================
echo Iniciando Soundboard AI em modo de diagnostico (--debug)
echo Logs detalhados serao exibidos neste console.
echo ========================================================
echo.

uv run --python 3.13 "%~dp0main.py" --debug %*
if errorlevel 1 (
    echo.
    echo [Erro] Ocorreu uma falha ao iniciar o Soundboard AI.
    pause
)