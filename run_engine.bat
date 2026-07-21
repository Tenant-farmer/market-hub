@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
.venv\Scripts\python -m src.trading.worker >> data\engine.log 2>&1
