@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo ==========================================
echo Check pyinstaller...
echo ==========================================
pyinstaller --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] pyinstaller not found. Please install it first:
    echo   pip install pyinstaller
    exit /b 1
)

echo.
echo ==========================================
echo Clean all Python bytecode caches...
echo ==========================================
for /d /r ".." %%d in (__pycache__) do (
    if exist "%%d" (
        echo Removing "%%d"
        rd /s /q "%%d"
    )
)
del /s /q "..\*.pyc" >nul 2>&1
del /s /q "..\*.pyo" >nul 2>&1

echo.
echo ==========================================
echo Remove old PyInstaller build artifacts...
echo ==========================================
for %%F in (DemoNewEMG DemoNewEEG DemoNewPPG SynchroniSDKPython_DemoNewEMG SynchroniSDKPython_DemoNewEEG SynchroniSDKPython_DemoNewPPG) do (
    if exist "build\%%F" (
        echo Removing "build\%%F"
        rd /s /q "build\%%F"
    )
    if exist "dist\%%F" (
        echo Removing "dist\%%F"
        rd /s /q "dist\%%F"
    )
    if exist "%%F.spec" (
        echo Removing "%%F.spec"
        del /f /q "%%F.spec"
    )
)

del /f /q "..\*.spec" >nul 2>&1

pip install -U synchroni-sensor-sdk

echo.
echo ==========================================
echo Build demos with PyInstaller...
echo ==========================================
set "COMMON_OPTS=--clean --noconfirm --onefile"

pyinstaller %COMMON_OPTS% --name DemoNewEMG SynchroniSDKPython_DemoNewEMG.py
if errorlevel 1 goto :error

pyinstaller %COMMON_OPTS% --name DemoNewEEG SynchroniSDKPython_DemoNewEEG.py
if errorlevel 1 goto :error

pyinstaller %COMMON_OPTS% --name DemoNewPPG SynchroniSDKPython_DemoNewPPG.py
if errorlevel 1 goto :error

echo.
echo ==========================================
echo All demos built successfully.
echo Output: %cd%\dist
 echo ==========================================
goto :eof

:error
echo.
echo ==========================================
echo [ERROR] Build failed!
echo ==========================================
exit /b 1
