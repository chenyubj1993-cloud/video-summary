@echo off
chcp 65001 >nul
rem Wrapper: forwards to cli.py with the bundled Python runtime.
rem Example:  run.cmd transcribe --bv BV1E9U8BBEvZ
set PYTHONUTF8=1
set NO_PROXY=*
set HF_ENDPOINT=https://hf-mirror.com
set PY=C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe
"%PY%" "%~dp0cli.py" %*
