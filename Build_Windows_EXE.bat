@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=C:\Python314\python.exe"
if not exist "%PYTHON_EXE%" (
  echo Python not found at %PYTHON_EXE%.
  echo Edit PYTHON_EXE in this script if Python is installed elsewhere.
  pause
  exit /b 1
)
echo Building SettAx v1.7...
if not exist ".build-venv\Scripts\python.exe" (
  "%PYTHON_EXE%" -m venv ".build-venv"
  if errorlevel 1 goto failed
)
".build-venv\Scripts\python.exe" -m pip install --upgrade pyinstaller
if errorlevel 1 goto failed
".build-venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name "SettAx" phonepe_settlement_tool.py
if errorlevel 1 goto failed
echo.
echo SUCCESS: %CD%\dist\SettAx.exe
echo Only the EXE in the dist folder is needed to run the application.
echo .build-venv and build folders can be deleted after a successful build.
echo.
echo Optional: Use Inno Setup with SettAx.iss to create an installer with uninstall support.
pause
exit /b 0
:failed
echo.
echo Build failed. Review the messages above.
pause
exit /b 1
