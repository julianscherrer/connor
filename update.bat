@echo off
setlocal
cd /d "%~dp0"
title Connor aktualisieren

echo  Hole die neuste Version von Connor ...
git pull
if errorlevel 1 (
    echo.
    echo  git pull hat nicht geklappt. Ist Git installiert und bist du online?
    pause
    exit /b 1
)
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    ".venv\Scripts\python.exe" -m pip install -r requirements-voice.txt
)
echo.
echo  Fertig! Starte Connor jetzt neu mit start.bat.
echo  Deine Daten im Ordner "data" bleiben dabei unveraendert.
pause