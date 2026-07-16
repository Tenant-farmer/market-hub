@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
.venv\Scripts\python -m src.jobs.hourly >> data\scheduler.log 2>&1
