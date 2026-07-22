@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "BUNDLE_ROOT=%~dp0"
set "BUNDLE_ROOT_ARG=%~dp0."
set "RUNTIME_PY=%BUNDLE_ROOT%runtime\python\python.exe"
set "BOOTSTRAP=%BUNDLE_ROOT%packaging\bootstrap-runtime.ps1"
set "HAD_ARGS=0"
if not "%~1"=="" set "HAD_ARGS=1"

:bootstrap
if not exist "%BOOTSTRAP%" (
  echo Missing bootstrap script: %BOOTSTRAP%
  exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%BOOTSTRAP%" -BundleRoot "%BUNDLE_ROOT_ARG%" -SkipEditor
set "BOOTSTRAP_EXIT=%ERRORLEVEL%"
if not "%BOOTSTRAP_EXIT%"=="0" (
  echo Portable Python bootstrap failed with exit code %BOOTSTRAP_EXIT%.
  set "EXIT_CODE=%BOOTSTRAP_EXIT%"
  goto done
)
if not exist "%RUNTIME_PY%" (
  echo Portable Python bootstrap completed without creating: %RUNTIME_PY%
  set "EXIT_CODE=1"
  goto done
)

:launch
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"

"%RUNTIME_PY%" -B -I "%BUNDLE_ROOT%app\launcher.py" %*
set "EXIT_CODE=%ERRORLEVEL%"
goto done

:done
if "%HAD_ARGS%"=="0" pause
exit /b %EXIT_CODE%
