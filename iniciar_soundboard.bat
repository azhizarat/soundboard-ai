@echo off
cd /d "%~dp0"

:: Se passado argumento de debug/diagnostico, mantem o console aberto
if "%~1"=="--debug" goto :run_debug
if "%~1"=="-d" goto :run_debug
if "%~1"=="--diagnostico" goto :run_debug
if "%~1"=="--help" goto :run_debug
if "%~1"=="-h" goto :run_debug

:: Inicializacao padrao: abre o Soundboard silenciosamente e fecha o terminal
start "" wscript //nologo "%~dp0Soundboard AI.vbs" %*
exit /b 0

:run_debug
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title Soundboard AI [Modo Diagnostico]
echo ========================================================
echo Iniciando Soundboard AI em modo de diagnostico (--debug)
echo Logs detalhados serao exibidos neste console.
echo ========================================================
echo.
uv run --python 3.13 "%~dp0main.py" %*
if errorlevel 1 (
    echo.
    echo [Erro] Ocorreu uma falha ao iniciar o Soundboard AI.
    pause
)