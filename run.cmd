@echo off
rem C2N boiler replacement assessment (Windows one-click launcher).
rem Default case: 600 MW subcritical. For another unit:
rem     run.cmd --config config\supercritical_1000mw.toml
rem If a virtual environment sits next to this file, it is used; otherwise PATH's python.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
if exist "%CD%\.venv\Scripts\python.exe" (
  set "PY=%CD%\.venv\Scripts\python.exe"
) else (
  set "PY=python"
)
"%PY%" run.py --config config\subcritical_600mw.toml %*
exit /b %ERRORLEVEL%
