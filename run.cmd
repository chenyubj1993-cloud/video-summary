@echo off
chcp 65001 >nul
rem Windows wrapper. Example:  run.cmd transcribe --bv BV1E9U8BBEvZ
rem Uses .venv\Scripts\python.exe when present, otherwise python from PATH.
rem Keep this file ASCII-only: cmd.exe parses it in the console codepage,
rem so UTF-8 Chinese in comments can swallow the following line.
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set NO_PROXY=*
set HF_ENDPOINT=https://hf-mirror.com
set PY=python
if exist "%~dp0.venv\Scripts\python.exe" set PY=%~dp0.venv\Scripts\python.exe
"%PY%" "%~dp0cli.py" %*
