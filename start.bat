@echo off
setlocal
cd /d "%~dp0"
title Connor

rem Bei neuen Paketen diese Zahl erhoehen - dann installiert start.bat sie automatisch nach.
set "SETUP_VERSION=2"

rem ---------------------------------------------------------------- Python
set "PY="
where py >nul 2>&1 && py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python -c "import sys" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo.
    echo  Python wurde nicht gefunden.
    echo  Installiere Python 3.10 oder neuer:
    echo    - https://www.python.org/downloads/   ^(Haken bei "Add python.exe to PATH" setzen^)
    echo    - oder im Terminal:  winget install -e --id Python.Python.3.12
    echo  Danach start.bat nochmal doppelklicken.
    echo.
    pause
    exit /b 1
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo  Connor braucht Python 3.10 oder neuer. Bitte Python aktualisieren.
    pause
    exit /b 1
)

rem ------------------------------------------------ Einrichtung und Updates
if not exist ".venv\Scripts\python.exe" (
    echo  Richte Connor beim ersten Start ein - das dauert ein paar Minuten ...
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
)
set "INSTALLED="
if exist ".venv\.installed" set /p INSTALLED=<".venv\.installed"
if not "%INSTALLED%"=="%SETUP_VERSION%" call :install
if errorlevel 1 goto :fail

rem ---------------------------------------------------------------- Ollama
where ollama >nul 2>&1
if errorlevel 1 if not exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
    echo.
    echo  Hinweis: Ollama ist noch nicht installiert - das ist Connors lokales KI-Gehirn.
    echo    Download: https://ollama.com/download   ^(oder: winget install -e --id Ollama.Ollama^)
    echo    Danach ein Modell laden, z. B.:  ollama pull qwen3:8b
    echo  Connor startet trotzdem und zeigt dir, was noch fehlt.
    echo.
    pause
)

start "" ".venv\Scripts\pythonw.exe" main.py %*
exit /b 0

:install
echo.
echo  Installiere bzw. aktualisiere Connors Pakete ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
echo.
echo  Installiere die Spracherkennung fuer das Diktieren - ca. 300 MB ...
".venv\Scripts\python.exe" -m pip install -r requirements-voice.txt
if errorlevel 1 (
    echo.
    echo  Hinweis: Die Spracherkennung liess sich nicht installieren.
    echo  Connor laeuft trotzdem, nur ohne Diktieren. Spaeter nochmal versuchen mit update.bat.
    echo.
    pause
)
>".venv\.installed" echo %SETUP_VERSION%
exit /b 0

:fail
echo.
echo  Beim Einrichten ist etwas schiefgelaufen - die Meldung steht oben.
echo  Tipp: Internetverbindung pruefen und start.bat nochmal starten.
pause
exit /b 1