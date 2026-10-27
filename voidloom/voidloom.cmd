@echo off
REM voidloom -- Windows shim for the LLM's memory toolkit.
REM Mirrors the POSIX `voidloom` bash shim: runs the package in place (no pip
REM install) by putting the package's PARENT dir on PYTHONPATH and invoking
REM `python -m voidloom`, so the toolkit runs straight from the source tree
REM alongside the brain. Install: copy/point this onto PATH, or add the dir that
REM contains voidloom\ to PATH.
setlocal EnableExtensions
set "PKG_DIR=%~dp0"
if "%PKG_DIR:~-1%"=="\" set "PKG_DIR=%PKG_DIR:~0,-1%"
for %%I in ("%PKG_DIR%\..") do set "PARENT_DIR=%%~fI"
set "PYTHONPATH=%PARENT_DIR%;%PYTHONPATH%"
rem CRUCIBLE-3 D7-F3 / U4: this line carried the UNIX redirect `>/dev/null 2>/dev/null`
rem in a BATCH file, so `where` could never succeed, the && branch never ran, and every
rem invocation fell through to `py -3` after printing a stray error. install.ps1:176
rem generates the CORRECT spelling -- the right version existed and the shipped copy
rem was never updated to match.
rem B2 class (iteration 2): THE INTERPRETER THE INSTALL CHOSE, not the first python on PATH. A Tier 1 that lives in
rem the brain's venv is invisible to PATH, so the CLI answered at Tier 0. Order, as the POSIX launcher:
rem   1. <brain>\INSTALL_RECORD interpreter= (written by install.sh; install.ps1 writes none today),
rem   2. <brain>\venv\Scripts\python.exe (the venv install.ps1 builds),
rem   3. python / py -3 on PATH, as before.
set "VL_PY="
if exist "%PARENT_DIR%\INSTALL_RECORD" for /f "usebackq tokens=1,* delims==" %%A in ("%PARENT_DIR%\INSTALL_RECORD") do if /i "%%A"=="interpreter" set "VL_PY=%%B"
if defined VL_PY if not exist "%VL_PY%" set "VL_PY="
if defined VL_PY if exist "%VL_PY%\*" set "VL_PY="
if not defined VL_PY if exist "%PARENT_DIR%\venv\Scripts\python.exe" set "VL_PY=%PARENT_DIR%\venv\Scripts\python.exe"
rem ERRORLEVEL is read on its OWN line: in one line with the call, %ERRORLEVEL% expands before the call runs.
if not defined VL_PY goto :vl_path_python
"%VL_PY%" -m voidloom %*
exit /b %ERRORLEVEL%
:vl_path_python
where python >nul 2>nul && (python -m voidloom %*) || (py -3 -m voidloom %*)
