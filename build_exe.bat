@echo off

setlocal

cd /d "%~dp0"



set "PYTHON="

where py >nul 2>&1 && py -3.13 -c "import sys" >nul 2>&1 && set "PYTHON=py -3.13"

if not defined PYTHON if exist "%LocalAppData%\Programs\Python\Python313\python.exe" (

    set "PYTHON=%LocalAppData%\Programs\Python\Python313\python.exe"

)

if not defined PYTHON (

    echo Python 3.13 is required to match the Archipelago release install.

    echo Install from https://www.python.org/downloads/ or run: py -3.13

    exit /b 1

)



echo Using: %PYTHON%

echo Installing build dependencies...

%PYTHON% -m pip install -r requirements.txt pyinstaller



echo Building ArchipelagoMappingEditor.exe...

%PYTHON% -m PyInstaller --noconfirm --clean mapping_editor.spec



if errorlevel 1 (

    echo Build failed.

    exit /b 1

)



echo.

echo Done: dist\ArchipelagoMappingEditor.exe

echo.

echo Note: users still need Archipelago installed. Set ARCHIPELAGO_PATH if auto-detect fails.

endlocal

