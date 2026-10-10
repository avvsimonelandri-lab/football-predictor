@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Football Predictor Pro 2.2 - creazione del programma

echo.
echo  ==========================================================
echo    Football Predictor Pro 2.2 - creazione del file .exe
echo  ==========================================================
echo.
echo  Ci vogliono 3-5 minuti la prima volta. Non chiudere la finestra.
echo.

rem ---- 1. trova Python -------------------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY goto :installa_python

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 goto :installa_python
goto :build

:installa_python
echo  Python non trovato: lo installo con winget (Microsoft Store App Installer)...
winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
    echo.
    echo  Installazione automatica non riuscita.
    echo  Scarica Python da https://www.python.org/downloads/  ^(spunta "Add python.exe to PATH"^)
    echo  e poi fai di nuovo doppio clic su CREA_EXE.bat
    pause
    exit /b 1
)
echo.
echo  Python installato. CHIUDI questa finestra e fai di nuovo doppio clic su CREA_EXE.bat
pause
exit /b 0

:build
echo  [1/3] Preparo l'ambiente...
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv
if errorlevel 1 goto :errore
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip >nul
echo  [2/3] Scarico le librerie...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :errore

echo  [3/3] Creo FootballPredictorPro.exe...
pyinstaller --noconfirm --clean --onefile --windowed --name FootballPredictorPro ^
    --collect-data customtkinter ^
    app.py
if errorlevel 1 goto :errore

rem la vecchia versione viene conservata come backup
if exist "FootballPredictorPro.exe" (
    if not exist "FootballPredictorPro_vecchia_versione.exe" ren "FootballPredictorPro.exe" "FootballPredictorPro_vecchia_versione.exe"
    if exist "FootballPredictorPro.exe" del /q "FootballPredictorPro.exe"
)
copy /y "dist\FootballPredictorPro.exe" "FootballPredictorPro.exe" >nul

echo.
echo  ==========================================================
echo    FATTO!  Il programma e' qui:
echo    %~dp0FootballPredictorPro.exe
echo  ==========================================================
echo.
echo  Se questa cartella e' "Desktop\APP CALCIO", il collegamento sul
echo  desktop apre gia' la nuova versione.
echo.
pause
exit /b 0

:errore
echo.
echo  Si e' verificato un errore. Fai uno screenshot di questa finestra
echo  e invialo a Claude per farlo correggere.
pause
exit /b 1
