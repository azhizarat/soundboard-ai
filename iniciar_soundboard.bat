@echo off
chcp 65001 >nul
title Soundboard AI - VB-Audio Cable Studio
cd /d "%~dp0"

echo Iniciando Soundboard AI...
uv run --python 3.13 "%~dp0main.py"
if errorlevel 1 (
    echo.
    echo [Erro] Ocorreu uma falha ao iniciar o Soundboard AI.
    pause
)
